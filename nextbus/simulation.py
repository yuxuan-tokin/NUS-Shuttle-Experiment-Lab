import copy
from dataclasses import asdict
import hashlib
import heapq
import math
import numpy as np
from .data import departures

FREE, WALK, QUEUE, BUS, ENGAGED, OUTSIDE, EARLY = range(7)
STATE_NAMES = ['free','walking','queue','onboard','engaged','outside','preclass']
ACTIVITY_CODES = {'class': 1, 'dining': 2, 'study': 3, 'home': 4}
MEAL_WINDOWS = ((420,660),(660,1050),(1050,1260))
MEAL_PEAKS = ((480,540),(705,795),(1095,1185))


def study_factor(time, tier, config):
    if time < config.study_evening_starts:
        return 1.
    return config.study_evening_up if tier in ('library','residence') else config.study_evening_down


def uniform(seed, *keys):
    payload = repr((seed, keys)).encode()
    return (int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(),'little') + .5) / 2**64


def class_profile(t, session, lead_minutes=15., post_start_peak_minutes=None, long_cutoff_minutes=40.):
    start,end = session.start,session.end
    duration = end-start
    if post_start_peak_minutes is None:
        cutoff = min(duration,15 if duration<50 else long_cutoff_minutes)
        if t<start-lead_minutes or t>=start+cutoff:
            return 0.
        if t<=start:
            return 1.
        return 1-(t-start)/cutoff
    # Reproduce the earlier ramp when a saved configuration specifies it.
    if duration <= 20:
        raise ValueError('Physical classes of 20 minutes or less need an explicit attractiveness rule')
    zero = end-20
    decline = start+min(post_start_peak_minutes,(duration-20)/2)
    if t <= start-lead_minutes or t >= zero:
        return 0.
    if t < start:
        return (t-start+lead_minutes)/lead_minutes
    if t < decline:
        return 1.
    return (zero-t)/(zero-decline)


def redistribute_class_scores(scores, log_multipliers, temperature):
    """Change class destination odds while preserving total class weight."""
    before = np.asarray(scores)/temperature
    after = before+log_multipliers
    correction = np.logaddexp.reduce(before)-np.logaddexp.reduce(after)
    return ((after+correction)*temperature).tolist()


class Simulation:
    def __init__(self, data, config):
        self.data, self.config = data, config
        self.sessions,self.excluded = data.sessions(config.day)
        multipliers = config.class_destination_multipliers
        unknown = multipliers.keys()-set(data.class_destination_zones.values())
        if unknown:
            raise ValueError(f'Unknown class destination zones: {sorted(unknown)}')
        self.class_destination_log_weights = {
            place: math.log(multipliers[zone]) for place,zone in data.class_destination_zones.items()
            if zone in multipliers
        }
        if any(getattr(config,key) is None for key in ('general_study_closes','residence_study_own_only','dining_meal_windows_are_hours')):
            raise ValueError('Confirm general study closing time, residence study access and dining opening rules in configuration before running')
        self.t = config.start
        self.events, self.serial = [], 0
        n = config.population
        frame = []
        for home,count in zip(data.homes,data.home_counts):
            frame.extend([home]*count)
        frame.extend([-1]*(30373-len(frame)))
        self.home = np.random.default_rng(config.population_seed).choice(np.array(frame), n, replace=False)
        self.loc = self.home.copy()
        self.state = np.where(self.home<0,OUTSIDE,FREE)
        self.goal = [None]*n
        self.leg = [None]*n
        self.release = np.full(n, config.start)
        self.entry = np.full(n,-1e9)
        self.entered = np.zeros(n,bool)
        self.meals = np.zeros(n,np.int8)
        self.postclass = np.zeros(n,bool)
        self.after_study = np.zeros(n,bool)
        self.queue_since = np.zeros(n)
        self.onbus = np.full(n,-1,int)
        self.motion = [None]*n
        self.closed = [{} for _ in range(n)]
        self.pending_meal = np.full(n,-1,int)
        self.meal_starts = np.zeros(3,int)
        self.campus_meals_by_person = np.zeros(n,np.uint8)
        self.dining_minutes_by_person = np.zeros(n)
        self.class_minutes_by_person = np.zeros(n)
        self.trip_started = np.zeros(n,bool)
        self.trip_long_feasible = np.zeros(n,bool)
        self.trip_2km = np.zeros(n,bool)
        self.trip_long_competitive = np.zeros(n,bool)
        self.trip_boarded = np.zeros(n,bool)
        self.completed_trips = self.completed_shuttle_trips = 0
        self.completed_long_feasible = self.completed_long_shuttle = 0
        self.completed_2km = self.completed_2km_shuttle = 0
        self.completed_2km_feasible = 0
        self.completed_long_competitive = 0
        self.departure_table = {route:departures(route) for route in data.routes}
        self.prediction_time = None
        self.predictions = {}
        self.queues = [[] for _ in range(data.stop_count)]
        self.buses, self.history, self.dispatch_log, self.decision_log = [], [], [], []
        self.paths = {}
        self.wait_total = 0.
        self.wait_records = []
        self.eligible_people, self.failed_people = set(), set()
        self.stop_integrals = np.zeros(data.stop_count)
        self.stop_over = np.zeros(data.stop_count)
        self.stop_peaks = np.zeros(data.stop_count,int)
        self.attendance = []
        self.series = []
        self.exit_share_at_check = None
        self.on_campus_return_share_at_check = None
        self.missed_classes = 0
        self.controller = None
        for route in data.routes:
            for index,time in enumerate(departures(route)):
                depot = data.depots[route]
                origin = data.routes[route][0]
                lead = data.distances[depot,origin]*config.road_multiplier/config.depot_speed_kmh*60
                if time-lead >= config.start:
                    self.schedule(time-lead, 2, 'launch', (route,index))
        for time in np.arange(config.start,config.end+1e-8,config.choice_interval):
            self.schedule(time,4,'choice',None)
        for time in np.arange(config.start,config.end+1e-8,config.observation_interval):
            self.schedule(time,7,'observe',None)
        for time in np.arange(config.start,config.end,config.decision_interval):
            self.schedule(time,8,'decide',None)
        self.schedule(config.exit_check,7,'exit_check',None)

    def schedule(self,time,priority,event,payload):
        if time < self.t - 1e-7:
            raise ValueError('Cannot schedule an event in the past')
        self.serial += 1
        heapq.heappush(self.events,(float(time),priority,self.serial,event,payload))

    def clone(self):
        controller = self.controller
        self.controller = None
        result = copy.deepcopy(self, {id(self.data): self.data})
        self.controller = controller
        return result

    def advance(self,end):
        if end < self.t:
            raise ValueError('Cannot run backwards')
        while self.events and self.events[0][0] <= end:
            time,_,_,event,payload = heapq.heappop(self.events)
            self.integrate_queues(time)
            self.t = time
            if event == 'launch': self.launch(*payload)
            elif event == 'bus_stop': self.bus_stop(*payload)
            elif event == 'board': self.board(*payload)
            elif event == 'arrive': self.arrive(payload)
            elif event == 'queue': self.join_queue(payload)
            elif event == 'release': self.free(payload)
            elif event == 'class_start': self.state[payload] = ENGAGED
            elif event == 'choice': self.choose()
            elif event == 'observe': self.observe()
            elif event == 'exit_check':
                self.exit_share_at_check = self.exit_share()
                self.on_campus_return_share_at_check = self.on_campus_return_share()
            elif event == 'decide' and self.controller is not None:
                action = self.controller.decide(self)
                self.execute(action)
        self.integrate_queues(end)
        self.t = end
        return self

    def integrate_queues(self,time):
        counts = np.array([len(q) for q in self.queues])
        dt = time-self.t
        self.wait_total += float(counts.sum()*dt)
        self.stop_integrals += counts*dt
        self.stop_over += (counts>self.config.queue_threshold)*dt
        self.stop_peaks = np.maximum(self.stop_peaks,counts)

    def free(self,i):
        if self.goal[i] and self.goal[i][0] == 'class':
            self.postclass[i] = True
        self.after_study[i] = bool(self.goal[i] and self.goal[i][0] == 'study' and self.state[i] == ENGAGED)
        self.state[i] = FREE
        self.goal[i] = None

    def gateway(self,xy_key,person,phase):
        weights = self.data.gateway_weights * np.exp(-2*self.data.distances[xy_key,self.data.gateways])
        u = uniform(self.config.seed,self.config.day,person,self.t,phase)
        index = min(len(weights)-1,int(np.searchsorted(np.cumsum(weights)/weights.sum(),u)))
        return self.data.gateways[index]

    def choose(self):
        t,c,d = self.t,self.config,self.data
        people = np.flatnonzero(((self.state==FREE)|(self.state==OUTSIDE)) & (self.release<=t))
        sessions = [(s,profile) for s in self.sessions
                    if (profile := class_profile(t,s,c.class_lead_minutes,c.class_post_start_peak_minutes,
                                                 c.class_long_cutoff_minutes))>0]
        class_zone_weights = [self.class_destination_log_weights.get(s.place,0.) for s,_ in sessions]
        redistribute_classes = any(class_zone_weights)
        meal = next((m for m,(a,b) in enumerate(MEAL_WINDOWS) if a<=t<b),None)
        meal_profile = meal_progress = 0.
        if meal is not None:
            a,b = MEAL_WINDOWS[meal]
            peak_start,peak_end = MEAL_PEAKS[meal]
            meal_profile = min(1.,(t-a)/(peak_start-a),(b-t)/(b-peak_end))
            # Pause the urgency ramp during the peak so the whole time bonus is flat.
            peak_duration = peak_end-peak_start
            peak_elapsed = min(max(t-peak_start,0),peak_duration)
            meal_progress = (t-a-peak_elapsed)/(b-a-peak_duration)
        evening = np.clip((t-1020)/240,0,1)
        morning_progress = np.clip((t-c.study_morning_ramp_start)/(c.study_morning_ramp_end-c.study_morning_ramp_start),0,1)
        study_total = c.study_total_weight*(c.study_morning_multiplier+(1-c.study_morning_multiplier)*morning_progress)
        morning_home = c.home_morning_boost if t<c.home_morning_until else 0.
        groups = {}
        for i in people:
            self.closed[i] = {key:end for key,end in self.closed[i].items() if end>t}
            key = (int(self.loc[i]),int(self.home[i]),int(self.meals[i]),bool(self.postclass[i]),bool(self.after_study[i]),self.entry[i]+120<=t,tuple(sorted(self.closed[i])))
            groups.setdefault(key,[]).append(int(i))
        for (origin,home,meals,postclass,after_study,can_return,closed),members in groups.items():
            at_home = origin == home
            ignore_class_distance = origin<0 or origin in d.class_distance_exempt
            options = [('stay',origin,-1)]
            boost = c.home_off_boost if home<0 else c.home_on_boost
            scores = [(4+boost)*evening if at_home else 0.]
            if at_home:
                scores[0] += morning_home
            for s,profile in sessions:
                penalty = 0 if ignore_class_distance else c.class_distance*d.distances[origin,s.place]
                scores.append(c.class_base_score+c.class_alpha*profile-penalty+c.temperature*c.class_size_eta*math.log(s.target/50))
                options.append(('class',s.place,s.id))
            if redistribute_classes:
                scores[1:] = redistribute_class_scores(scores[1:],class_zone_weights,c.temperature)
            for j in d.activities:
                place = d.places[j]
                if place.kind!='dining': continue
                if ('dining',j) in closed: continue
                if meal is None or meals & (1<<meal) or not place.meals[meal]: continue
                if place.home>=0 and place.home!=home: continue
                scale = c.breakfast_factor if meal==0 else 1
                plan = 1+c.meal_plan_bonus*(place.home==home and home>=0)
                value = c.dining_base_score+c.meal_score_adjustments[meal]+scale*(c.meal_peak_strength*meal_profile*plan+c.meal_urgency*meal_progress)
                destination = place.destination
                scores.append(value-(0 if origin<0 else 3*d.distances[origin,destination]))
                options.append(('dining',destination,j))
            study_destinations, study_log_weights = [], []
            for destination,site in d.study_sites.items():
                if ('study',destination) in closed: continue
                if site['home']>=0 and c.residence_study_own_only and site['home']!=home: continue
                hours = site['hours'] or (0,c.general_study_closes if c.general_study_closes is not None else 1440)
                if not hours[0]<=t<hours[1]: continue
                study_destinations.append(destination)
                penalty = 0 if origin<0 else 2*d.distances[origin,destination]/c.temperature
                weight = c.study_weights[site['tier']]*study_factor(t,site['tier'],c)
                study_log_weights.append(math.log(weight)-penalty)
            if study_destinations and study_total > 0:
                quality = np.exp(np.array(study_log_weights)-max(study_log_weights))
                shares = quality/quality.sum()
                if after_study and origin in study_destinations:
                    shares *= 1-c.study_continuation_share
                    shares[study_destinations.index(origin)] += c.study_continuation_share
                for destination,share in zip(study_destinations,shares):
                    if share > 0:
                        scores.append(c.temperature*(math.log(study_total)+math.log(share)))
                        options.append(('study',destination,-1))
            if not at_home and (home>=0 or can_return):
                score = -3+(4+boost)*evening+morning_home
                if home<0 and not postclass:
                    total = np.sum(d.gateway_weights*np.exp(-2*d.distances[origin,d.gateways]))/d.gateway_weights.sum()
                    score += c.temperature*math.log(total)
                options.append(('home',home,-1))
                scores.append(score)
            weights = np.exp((np.array(scores)-max(scores))/c.temperature)
            # Stay is its own alternative. All activities and sessions at a
            # building contribute to the same destination draw.
            by_building = {}
            for index,(kind,destination,_) in enumerate(options):
                key = None if kind=='stay' else destination
                by_building.setdefault(key,[]).append(index)
            # Contiguous activity intervals subdivide each building's interval.
            # One cumulative draw is equivalent to the building and activity draw.
            ordered = np.fromiter((i for indices in by_building.values() for i in indices),dtype=int)
            cumulative = np.cumsum(weights[ordered])
            draws = np.fromiter((uniform(c.seed,c.day,i,t,'choice') for i in members),dtype=float)
            selected = np.minimum(len(ordered)-1,np.searchsorted(cumulative,draws*cumulative[-1],side='right'))
            for i,index in zip(members,ordered[selected]):
                option = options[index]
                self.postclass[i] = False
                if option[0]=='stay': continue
                self.after_study[i] = False
                kind,destination,extra = option
                if origin<0:
                    self.loc[i] = self.gateway(destination,i,'entry')
                    self.entry[i],self.entered[i] = t,True
                if kind=='home' and home<0:
                    destination = self.gateway(int(self.loc[i]),i,'exit')
                if kind=='dining':
                    self.meals[i] |= 1<<meal
                    self.pending_meal[i] = meal
                self.goal[i] = (kind,destination,extra)
                self.travel(i,destination)

    def walk_time(self,a,b):
        return self.data.distances[a,b]*1000*self.config.walk_multiplier/self.config.walk_metres_per_minute

    def itineraries(self,origin,destination):
        key = origin,destination
        if key in self.paths: return self.paths[key]
        d = self.data
        paths = []
        for ai,a in enumerate(d.places[origin].stops):
            for bi,b in enumerate(d.places[destination].stops):
                for route,seq in d.routes.items():
                    for start in [k for k,s in enumerate(seq[:-1]) if s==a]:
                        for end in [k for k,s in enumerate(seq) if s==b and k>start]:
                            ride = sum(d.segment_times[x,y] for x,y in zip(seq[start:end],seq[start+1:end+1]))
                            total = self.walk_time(origin,a)+ride+self.walk_time(b,destination)
                            flexible_origin = 'Ridge View' in d.places[origin].name or d.places[origin].id in ('ACT:DIN-RVRC','ACT:DIN-SUPPER')
                            flexible_destination = 'Ridge View' in d.places[destination].name or d.places[destination].id in ('ACT:DIN-RVRC','ACT:DIN-SUPPER')
                            priority = (0 if flexible_origin else ai)+(0 if flexible_destination else bi)
                            paths.append((priority,total,route,start,end,a,b))
        paths.sort()
        self.paths[key] = paths
        return paths

    def available(self,path,at):
        _,_,route,start,end,_,_ = path
        seq = self.data.routes[route]
        nominal = sum(self.data.segment_times[a,b] for a,b in zip(seq[:start],seq[1:start+1]))
        if any(t>=self.t and t+nominal>=at for t in self.departure_table[route]):
            return True
        for bus in self.buses:
            if bus['route']==route and bus['index']<start and bus['finish'] is None:
                remaining = sum(self.data.segment_times[a,b] for a,b in zip(seq[max(0,bus['index']+1):start],seq[max(0,bus['index']+1)+1:start+1]))
                if bus['next_time']+remaining>=at: return True
            if bus['route']==route and bus['index']==start and bus.get('boarding',False) and at<=self.t:
                return True
        return False

    def expected_wait(self,path,at):
        if self.prediction_time != self.t:
            self.prediction_time = self.t
            self.predictions = {}
            for route,seq in self.data.routes.items():
                offsets = np.concatenate(([0],np.cumsum([self.data.segment_times[a,b] for a,b in zip(seq,seq[1:])])))
                for index,offset in enumerate(offsets):
                    self.predictions[route,index] = [t+float(offset) for t in self.departure_table[route] if t>=self.t]
            for bus in self.buses:
                if bus['finish'] is not None: continue
                route = bus['route']
                seq = self.data.routes[route]
                index = bus['index']
                if index<0:
                    time,index = bus['start'],0
                elif bus.get('boarding',False):
                    time = self.t
                elif index<len(seq)-1:
                    # Observe actual progress already made, then assume nominal future speed.
                    elapsed = self.t-bus['segment_start']
                    fraction = np.clip(elapsed/(bus['next_time']-bus['segment_start']),0,1)
                    time = self.t+(1-fraction)*self.data.segment_times[seq[index],seq[index+1]]
                    index += 1
                else:
                    continue
                for k in range(index,len(seq)):
                    self.predictions[route,k].append(float(time))
                    if k<len(seq)-1: time += self.data.segment_times[seq[k],seq[k+1]]
            for values in self.predictions.values(): values.sort()
        values = self.predictions[path[2],path[3]]
        from bisect import bisect_left
        index = bisect_left(values,at-1e-9)
        return max(0,values[index]-at) if index<len(values) else None

    def travel(self,i,destination):
        origin = int(self.loc[i])
        feasible = []
        for path in self.itineraries(origin,destination):
            at = self.t+self.walk_time(origin,path[5])
            wait = self.expected_wait(path,at)
            if wait is not None: feasible.append((path,wait))
        self.trip_started[i] = origin!=destination
        self.trip_long_feasible[i] = bool(feasible) and self.data.distances[origin,destination]>=1.
        self.trip_2km[i] = self.data.distances[origin,destination]>=2.
        walk = self.walk_time(origin,destination)
        self.trip_long_competitive[i] = self.trip_long_feasible[i] and any(path[1]<=walk for path,_ in feasible)
        self.trip_boarded[i] = False
        selected = min(feasible,key=lambda item:(item[0][1]+item[1],item[0][0],item[0][2])) if feasible else None
        path,wait = selected if selected else (None,0)
        if path and path[1]+wait<=walk:
            self.leg[i] = path
            self.start_walk(i,origin,path[5],'queue')
        else:
            self.leg[i] = None
            self.start_walk(i,origin,destination,'arrive')

    def start_walk(self,i,origin,destination,event):
        end = self.t+self.walk_time(origin,destination)
        self.state[i] = WALK
        self.motion[i] = (origin,destination,self.t,end)
        self.schedule(end,3,event,i)

    def join_queue(self,i):
        self.loc[i] = self.leg[i][5]
        self.motion[i] = None
        if not self.available(self.leg[i],self.t):
            self.start_walk(i,int(self.loc[i]),self.goal[i][1],'arrive')
            return
        self.state[i] = QUEUE
        self.queue_since[i] = self.t
        self.queues[int(self.loc[i])].append(i)
        self.stop_peaks[int(self.loc[i])] = max(self.stop_peaks[int(self.loc[i])],len(self.queues[int(self.loc[i])]))

    def arrive(self,i):
        kind,destination,extra = self.goal[i]
        if self.trip_started[i]:
            self.completed_trips += 1
            self.completed_shuttle_trips += int(self.trip_boarded[i])
            self.completed_long_feasible += int(self.trip_long_feasible[i])
            self.completed_long_shuttle += int(self.trip_long_feasible[i] and self.trip_boarded[i])
            self.completed_2km += int(self.trip_2km[i])
            self.completed_2km_feasible += int(self.trip_2km[i] and self.trip_long_feasible[i])
            self.completed_2km_shuttle += int(self.trip_2km[i] and self.trip_boarded[i])
            self.completed_long_competitive += int(self.trip_long_competitive[i])
            self.trip_started[i] = False
        self.loc[i],self.motion[i] = destination,None
        if kind=='home':
            if self.home[i]<0:
                self.loc[i],self.state[i],self.release[i] = -1,OUTSIDE,self.t+120
            else:
                self.state[i] = FREE
            self.goal[i] = None
        elif kind=='class':
            session = self.sessions[extra]
            if self.t>=session.end:
                self.missed_classes += 1
                self.free(i)
            else:
                self.class_minutes_by_person[i] += max(0,min(session.end,self.config.end)-max(self.t,session.start))
                self.state[i] = EARLY if self.t<session.start else ENGAGED
                if self.t<session.start: self.schedule(session.start,0,'class_start',i)
                self.schedule(session.end,0,'release',i)
        else:
            if kind=='study':
                site = self.data.study_sites[destination]
                hours = site['hours'] or (0,self.config.general_study_closes if self.config.general_study_closes is not None else 1440)
                is_open = hours[0]<=self.t<hours[1]
                closed_key = ('study',destination)
            else:
                place = self.data.places[extra]
                if place.id in ('ACT:DIN-KRMRT','ACT:DIN-SUPPER'):
                    is_open = True
                else:
                    is_open = any(flag and a<=self.t<b for flag,(a,b) in zip(place.meals,MEAL_WINDOWS)) if self.config.dining_meal_windows_are_hours else True
                closed_key = ('dining',extra)
            if not is_open:
                next_open = 1440
                if kind=='dining':
                    next_open = next((a for flag,(a,b) in zip(place.meals,MEAL_WINDOWS) if flag and a>self.t),1440)
                    self.meals[i] &= ~(1<<int(self.pending_meal[i]))
                    self.pending_meal[i] = -1
                self.closed[i][closed_key] = next_open
                self.free(i)
                return
            self.state[i] = ENGAGED
            duration = 30
            if kind=='dining':
                meal = self.pending_meal[i]
                durations = self.config.dining_minutes
                duration = durations[meal] if isinstance(durations,list) else durations
                self.meal_starts[meal] += 1
                self.dining_minutes_by_person[i] += max(0,min(duration,self.config.end-self.t))
                if place.id not in ('ACT:DIN-KRMRT','ACT:DIN-SUPPER'):
                    self.campus_meals_by_person[i] |= 1 << int(meal)
            self.schedule(self.t+duration,0,'release',i)

    def launch(self,route,index,extra=False):
        origin = self.data.routes[route][0]
        depot = self.data.depots[route]
        lead = self.data.distances[depot,origin]*self.config.road_multiplier/self.config.depot_speed_kmh*60
        uid = f'extra-{len(self.dispatch_log)}' if extra else f'{route}-{index}'
        bus = dict(id=uid,route=route,extra=extra,dispatch=self.t,start=self.t+lead,finish=None,index=-1,next_time=self.t+lead,passengers=[],last=self.t+lead,service_minutes=0.,passenger_minutes=0.,crowded_minutes=0.,km=0.,boarding=False)
        self.buses.append(bus)
        bus_id = len(self.buses)-1
        if extra: self.dispatch_log.append(dict(time=self.t,route=route,bus_id=uid))
        self.schedule(self.t+lead,2,'bus_stop',(bus_id,0))
        return bus_id

    def accrue_bus(self,bus):
        dt = max(0,self.t-bus['last'])
        bus['service_minutes'] += dt
        bus['passenger_minutes'] += dt*len(bus['passengers'])
        bus['crowded_minutes'] += dt*(len(bus['passengers'])>=66)
        bus['last'] = self.t

    def bus_stop(self,bus_id,index):
        bus = self.buses[bus_id]
        self.accrue_bus(bus)
        bus['index'],bus['boarding'] = index,True
        seq = self.data.routes[bus['route']]
        for i in list(bus['passengers']):
            if self.leg[i][4]==index:
                bus['passengers'].remove(i)
                self.loc[i],self.onbus[i] = seq[index],-1
                self.start_walk(i,seq[index],self.goal[i][1],'arrive')
        self.schedule(self.t,6,'board',(bus_id,index))

    def board(self,bus_id,index):
        bus = self.buses[bus_id]
        seq = self.data.routes[bus['route']]
        remaining = []
        for i in self.queues[seq[index]]:
            path = self.leg[i]
            eligible = path[2]==bus['route'] and path[3]==index
            if not eligible:
                remaining.append(i)
                continue
            self.eligible_people.add(i)
            if len(bus['passengers'])>=self.config.capacity:
                self.failed_people.add(i)
                remaining.append(i)
                continue
            self.wait_records.append((i,self.queue_since[i],self.t,'boarded'))
            self.state[i],self.onbus[i] = BUS,bus_id
            self.trip_boarded[i] = True
            bus['passengers'].append(i)
        self.queues[seq[index]] = remaining
        bus['boarding'] = False
        if index==len(seq)-1:
            bus['service_end'] = self.t
            back = self.data.distances[seq[-1],self.data.depots[bus['route']]]*self.config.road_multiplier/self.config.depot_speed_kmh*60
            bus['finish'] = self.t+back
        else:
            a,b = seq[index:index+2]
            sigma = self.config.sigma_peak if any(a<=self.t<b for a,b in self.config.peak_bands) else self.config.sigma_off
            u1 = max(1e-15,uniform(self.config.seed,self.config.day,bus['id'],index,'delay1'))
            u2 = uniform(self.config.seed,self.config.day,bus['id'],index,'delay2')
            noise = max(0,sigma*math.sqrt(-2*math.log(u1))*math.cos(2*math.pi*u2))
            bus['next_time'] = self.t+self.data.segment_times[a,b]+noise
            bus['segment_start'] = self.t
            bus['km'] += self.data.distances[a,b]*self.config.road_multiplier
            self.schedule(bus['next_time'],2,'bus_stop',(bus_id,index+1))
        self.rescue_queues(bus['route'])

    def rescue_queues(self,route):
        if self.t<self.departure_table[route][-1]: return
        for stop,queue in enumerate(self.queues):
            keep = []
            for i in queue:
                if self.leg[i][2]!=route or self.available(self.leg[i],self.t):
                    keep.append(i)
                else:
                    self.wait_records.append((i,self.queue_since[i],self.t,'walk_after_service'))
                    self.start_walk(i,stop,self.goal[i][1],'arrive')
            self.queues[stop] = keep

    def execute(self,action):
        valid = isinstance(action,dict) and set(action)=={'dispatch','route_id','quantity'}
        valid = valid and type(action.get('dispatch')) is bool and type(action.get('quantity')) is int
        valid = valid and ((action['dispatch'] and action['quantity']==1 and isinstance(action['route_id'],str) and action['route_id'] in self.data.routes) or (not action['dispatch'] and action['quantity']==0 and action['route_id'] is None))
        self.decision_log.append(dict(time=self.t,action=action,valid=bool(valid)))
        if valid and action['dispatch']: self.launch(action['route_id'],0,True)
        return bool(valid)

    def observation(self):
        vehicles,commitments = [],[]
        for bus in self.buses:
            if bus['finish'] is not None and bus['finish']<=self.t: continue
            status = 'en_route_from_depot' if self.t<bus['start'] else 'returning' if 'service_end' in bus else 'in_service'
            if status=='in_service':
                vehicles.append(dict(run_id=bus['id'],route_id=bus['route'],stop_index=bus['index'],crowd_level='HIGH' if len(bus['passengers'])>=66 else 'LOW' if len(bus['passengers'])<=22 else 'MEDIUM',service_type='extra' if bus['extra'] else 'scheduled',position=self.bus_position(bus)))
            if bus['extra']:
                commitments.append(dict(dispatch_id=bus['id'],route_id=bus['route'],dispatched_at=bus['dispatch'],status=status))
        return dict(time=self.t,vehicles=vehicles,extra_bus_commitments=commitments)

    def bus_position(self,bus):
        seq = self.data.routes[bus['route']]
        if self.t<bus['start']:
            a,b = self.data.depots[bus['route']],seq[0]
            start,end = bus['dispatch'],bus['start']
        elif 'service_end' in bus:
            a,b = seq[-1],self.data.depots[bus['route']]
            start,end = bus['service_end'],bus['finish']
        else:
            index = max(0,bus['index'])
            a,b = seq[index],seq[min(index+1,len(seq)-1)]
            start,end = bus.get('segment_start',self.t),bus['next_time']
        fraction = np.clip((self.t-start)/max(end-start,1e-9),0,1)
        return (self.data.xy[a]*(1-fraction)+self.data.xy[b]*fraction).tolist()

    def observe(self):
        self.history.append(self.observation())
        actual = np.zeros(len(self.sessions))
        for i in np.flatnonzero(self.state==ENGAGED):
            if self.goal[i] and self.goal[i][0]=='class': actual[self.goal[i][2]]+=1
        buildings = {}
        for s,count in zip(self.sessions,actual):
            key = self.data.places[s.place].building
            values = buildings.setdefault(key,[0.,0.])
            values[0] += count
            values[1] += s.target if s.start<=self.t<s.end else 0
        self.attendance.append(dict(time=self.t,buildings=buildings,sessions=actual.tolist()))
        self.series.append(dict(time=self.t,queue=[len(q) for q in self.queues],states=np.bincount(self.state,minlength=7).tolist()))

    def exit_share(self):
        entrants = self.entered & (self.home<0)
        return float(np.mean(self.state[entrants]==OUTSIDE)) if entrants.any() else None

    def on_campus_return_share(self):
        residents = self.home>=0
        present = (self.loc==self.home) & np.isin(self.state,(FREE,ENGAGED,EARLY))
        return float(np.mean(present[residents])) if residents.any() else None

    def bus_metrics(self,bus):
        finish = min(self.t,bus.get('service_end',self.t))
        tail = max(0,finish-bus['last'])
        return (bus['service_minutes']+tail,bus['passenger_minutes']+tail*len(bus['passengers']),bus['crowded_minutes']+tail*(len(bus['passengers'])>=66))

    def metrics(self):
        durations = [end-start for _,start,end,_ in self.wait_records]
        censored = [self.t-self.queue_since[i] for q in self.queues for i in q]
        all_waits = durations+censored
        extra_hours = sum(max(0,min(self.t,b['finish'] if b['finish'] is not None else self.t)-b['dispatch'])/60 for b in self.buses if b['extra'])
        service,passengers,crowded = 0.,0.,0.
        total_service,total_crowded = 0.,0.
        for bus in self.buses:
            a,b,c = self.bus_metrics(bus)
            total_service+=a; total_crowded+=c
            if bus['extra']: service+=a; passengers+=b; crowded+=c
        km = 0.
        for bus in self.buses:
            if not bus['extra']: continue
            seq = self.data.routes[bus['route']]
            origin = self.data.depots[bus['route']]
            lead_dist = self.data.distances[origin,seq[0]]*self.config.road_multiplier
            km += lead_dist*min(1,max(0,(self.t-bus['dispatch'])/max(bus['start']-bus['dispatch'],1e-9)))
            upto = max(0,bus['index'])
            km += sum(self.data.distances[a,b]*self.config.road_multiplier for a,b in zip(seq[:upto],seq[1:upto+1]))
            if 'service_end' in bus:
                km += self.data.distances[seq[-1],origin]*self.config.road_multiplier*min(1,max(0,(self.t-bus['service_end'])/max(bus['finish']-bus['service_end'],1e-9)))
            elif bus['index']>=0 and bus['index']<len(seq)-1:
                fraction = np.clip((self.t-bus.get('segment_start',self.t))/max(bus['next_time']-bus.get('segment_start',self.t),1e-9),0,1)
                km += self.data.distances[seq[upto],seq[upto+1]]*self.config.road_multiplier*fraction
        return dict(waiting_passenger_minutes=self.wait_total,wait_p95_observed=float(np.percentile(all_waits,95)) if all_waits else None,long_wait_share_observed=float(np.mean(np.array(all_waits)>self.config.long_wait_minutes)) if all_waits else None,unfinished_waits=len(censored),capacity_failure_rate=len(self.failed_people)/len(self.eligible_people) if self.eligible_people else None,capacity_failed_people=len(self.failed_people),eligible_people=len(self.eligible_people),queue_peak=self.stop_peaks.tolist(),queue_minutes_above_threshold=self.stop_over.tolist(),queue_passenger_minutes=self.stop_integrals.tolist(),crowded_bus_minutes=total_crowded,crowded_bus_time_share=total_crowded/total_service if total_service else None,extra_bus_hours=extra_hours,extra_dispatch_count=len(self.dispatch_log),extra_km=float(km),extra_bus_utilization=passengers/(self.config.capacity*service) if service else None,exit_share=self.exit_share_at_check,on_campus_return_share=self.on_campus_return_share_at_check,meal_participation=dict(zip(('breakfast','lunch','dinner'),(self.meal_starts/self.config.population).tolist())),missed_classes=self.missed_classes,population=int(len(self.state)))

    def snapshot(self):
        points = self.data.xy[np.maximum(self.loc,0)].copy()
        for i in np.flatnonzero(self.state==WALK):
            a,b,start,end = self.motion[i]
            fraction = np.clip((self.t-start)/max(end-start,1e-9),0,1)
            points[i] = self.data.xy[a]*(1-fraction)+self.data.xy[b]*fraction
        buses = []
        for j,bus in enumerate(self.buses):
            if bus['finish'] is not None and bus['finish']<self.t: continue
            xy = self.bus_position(bus)
            points[self.onbus==j] = xy
            status = 'depot travel' if self.t<bus['start'] else 'returning' if 'service_end' in bus else 'passenger service'
            buses.append(dict(id=bus['id'],route=bus['route'],xy=xy,extra=bus['extra'],load=len(bus['passengers']),status=status))
        visible = np.flatnonzero(self.state!=OUTSIDE)
        people = []
        activity_counts = dict(class_attending=0,dining=0,studying=0,at_residence=0,queuing=int(np.sum(self.state==QUEUE)))
        for i in visible:
            goal = self.goal[i]
            activity = ACTIVITY_CODES.get(goal[0],0) if goal else (4 if self.loc[i]==self.home[i] else 0)
            destination = goal[1] if goal else int(self.loc[i])
            people.append([int(i),*points[i].tolist(),int(self.state[i]),activity,int(destination)])
            if self.state[i]==ENGAGED:
                key = {1:'class_attending',2:'dining',3:'studying'}.get(activity)
                if key: activity_counts[key] += 1
            elif activity==4 and self.state[i]==FREE:
                activity_counts['at_residence'] += 1
        return dict(time=self.t,people=people,buses=buses,activity_counts=activity_counts,metrics=self.metrics(),observation=self.observation())
