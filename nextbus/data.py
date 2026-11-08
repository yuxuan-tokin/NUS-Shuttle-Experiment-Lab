from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import re
import numpy as np
import openpyxl


def distance(a, b):
    a, b = np.radians(a), np.radians(b)
    v = np.sin((b[..., 0] - a[..., 0]) / 2)**2 + np.cos(a[..., 0]) * np.cos(b[..., 0]) * np.sin((b[..., 1] - a[..., 1]) / 2)**2
    return 6371 * 2 * np.arcsin(np.sqrt(np.clip(v, 0, 1)))


def minute(value):
    if hasattr(value, "total_seconds"):
        return value.total_seconds() / 60
    text = str(value).replace(":", "").zfill(4)
    return int(text[:2]) * 60 + int(text[2:4])


@dataclass
class Place:
    id: str
    name: str
    kind: str
    xy: tuple
    stops: list
    building: str = ""
    meals: tuple = (0, 0, 0)
    hours: tuple = (0, 1440)
    home: int = -1
    destination: int = -1


@dataclass
class Session:
    id: int
    place: int
    start: float
    end: float
    target: float
    row: int


ROUTES = {
    "A1": "KRB LT13 AS5 BIZ2 TCOMS-OPP PGP KR-MRT LT27 UHALL UHC-OPP YIH CLB KRB",
    "A2": "KRB IT YIH-OPP MUSEUM UHC UHALL-OPP S17 KR-MRT-OPP PGPR TCOMS HSSML-OPP NUSS-OPP LT13-OPP KRB",
    "D1": "COM3 HSSML-OPP NUSS-OPP LT13-OPP IT YIH-OPP MUSEUM UTOWN YIH CLB LT13 AS5 BIZ2 COM3",
    "D2": "COM3 TCOMS-OPP PGP KR-MRT LT27 UHALL UHC-OPP MUSEUM UTOWN UHC UHALL-OPP S17 KR-MRT-OPP PGPR TCOMS COM3",
    "K": "PGP KR-MRT LT27 UHALL UHC-OPP YIH CLB SDE3-OPP JP-SCH-16151 KV MUSEUM UHC UHALL-OPP S17 KR-MRT-OPP PGPR",
    "R1": "KV MUSEUM UTOWN YIH CLB LT13 AS5 BIZ2 PGP",
    "R2": "PGP HSSML-OPP NUSS-OPP LT13-OPP IT YIH-OPP UTOWN RAFFLES KV",
}
# Inclusive start of each band, midpoint headway. A1 gap carries preceding band.
BANDS = {
    "A1": [(435,9),(481,5),(526,11),(546,5.5),(586,9),(676,6.5),(856,9),(1036,6),(1171,15),(1246,10),(1275,15)],
    "A2": [(435,7),(601,9.5),(676,7),(841,8.5),(931,6.5),(961,9.5),(1021,6.5),(1171,15),(1246,10),(1276,15)],
    "D1": [(435,9),(481,7),(556,6.5),(841,7.5),(1036,7),(1171,15)],
    "D2": [(435,6),(601,9),(676,6),(841,7),(961,9.5),(1021,6),(1081,7),(1171,14),(1231,11.5),(1321,15)],
}


def departures(route):
    if route == "K":
        return list(range(424, 1385, 40))
    if route == "R1":
        return list(range(460, 1061, 20)) + [1085,1110,1130,1150,1170]
    if route == "R2":
        return list(range(500, 1081, 20)) + [1110,1130,1150,1170]
    result = [435.0]
    while result[-1] < 1380:
        headway = [h for start, h in BANDS[route] if start <= result[-1]][-1]
        result.append(min(1380, result[-1] + headway))
    return result


class Dataset:
    def __init__(self, config):
        self.config = config
        with open(config.workbook, "rb") as stream:
            self.digest = hashlib.sha256(stream.read()).hexdigest()
        book = openpyxl.load_workbook(config.workbook, read_only=True, data_only=True)
        rows = [r for r in book["Locations"].values if len(r) > 5 and r[0]]
        self.places, self.stop_codes, self.opposite, self.venues = [], {}, {}, {}
        self.homes, self.home_counts, self.warnings = [], [], []
        def add(row, kind):
            if row[4] is None or row[5] is None:
                return None
            idx = len(self.places)
            self.places.append(Place(str(row[0]), str(row[2]), kind, (float(row[4]),float(row[5])), [], str(row[13] or row[3] or row[2])))
            return idx
        for r in rows:
            if r[1] == "Shuttle stop":
                idx = add(r, "stop")
                self.stop_codes[r[3]] = idx
                self.opposite[r[3]] = r[12]
        self.routes = {r: [self.stop_codes[s] for s in sequence.split()] for r, sequence in ROUTES.items()}
        self.stop_count = len(self.stop_codes)
        for r in rows:
            if r[1] not in ("Residence", "Venue"):
                continue
            idx = add(r, r[1].lower())
            if idx is None:
                continue
            if r[1] == "Venue":
                self.venues[str(r[3])] = idx
            else:
                self.homes.append(idx)
                count = 475 if "Hall" in r[2] else 1500 if "NUS College" in r[2] else 750 if "House" in r[2] or "PGPR" in r[2] else 600
                self.home_counts.append(count)
            nearest = r[8]
            self.places[idx].stops = [self.stop_codes[s] for s in [nearest,self.opposite.get(nearest)] if s in self.stop_codes]
            if "Ridge View" in r[2]:
                self.places[idx].stops = [self.stop_codes[s] for s in ["UHALL-OPP","UHC","UHC-OPP","UHALL"]]
        utown = self.stop_codes["UTOWN"]
        self.homes.append(len(self.places))
        self.home_counts.append(1700)
        self.places.append(Place("RES:UTR", "UTown Residence (provisional anchor)", "residence", self.places[utown].xy, [utown]))
        self.activities = []
        for r in book["Dining and Study"].values:
            if len(r) < 18 or r[1] not in ("Dining", "Study"):
                continue
            stops = [self.stop_codes[s.strip()] for s in str(r[8] or "").split(";") if s.strip() in self.stop_codes]
            if r[0] == "ACT:DIN-TECHNO":
                stops = [self.stop_codes["IT"],self.stop_codes["CLB"]]
            meals = tuple(int(v or 0) for v in r[12:15])
            if r[0] == "ACT:DIN-YIH":
                meals = (1,1,1)
            home = -1
            if "meal plan" in r[2] or "dining hall" in r[2]:
                name = r[2].replace(" meal plan", "").replace(" dining hall", "")
                if name == "RVRC":
                    name = "Ridge View Residential College"
                home = next((h for h in self.homes if self.places[h].name == name), -1)
                if home < 0:
                    raise ValueError(f"Meal-plan home unmatched: {name}")
            hours = (minute(r[15]),minute(r[16])) if r[1] == "Study" else (0,1440)
            self.activities.append(len(self.places))
            self.places.append(Place(r[0], r[2], r[1].lower(), (float(r[3]),float(r[4])), stops, r[6], meals, hours, home))
        self.class_rows = [r for r in book["Classes"].values if len(r)>12 and isinstance(r[0], (int,float))]
        self._sessions_by_day = {}
        book.close()
        opposite_indices = {
            index:self.stop_codes[self.opposite[code]]
            for code,index in self.stop_codes.items()
            if self.opposite[code] in self.stop_codes
        }
        self.buildings = {}
        # Rooms identify sessions. General activities receive one option per building.
        groups = {}
        for idx in self.venues.values():
            groups.setdefault(self.places[idx].building, []).append(idx)
        for name,indices in groups.items():
            primary = indices[0]
            self.buildings[name] = primary
            self.places[primary].name = name
            self.places[primary].xy = tuple(np.mean([self.places[i].xy for i in set(indices)],axis=0))
            candidates = {stop for idx in indices for stop in self.places[idx].stops}
            if candidates:
                nearest = min(candidates, key=lambda stop:(
                    distance(self.places[primary].xy,self.places[stop].xy),stop))
                self.places[primary].stops = [nearest]
                if nearest in opposite_indices:
                    self.places[primary].stops.append(opposite_indices[nearest])
            for code,idx in list(self.venues.items()):
                if idx in indices:
                    self.venues[code] = primary
        # A residence's reference venue is an explicit workbook association.
        for row in rows:
            if row[1] != 'Residence':
                continue
            home = next((h for h in self.homes if self.places[h].id==row[0]),None)
            associated = self.venues.get(str(row[7]))
            if home is not None and associated is not None:
                for code,idx in list(self.venues.items()):
                    if idx==associated: self.venues[code] = home
                for name,idx in list(self.buildings.items()):
                    if idx==associated: self.buildings[name] = home
        self.study_sites = {}
        for idx in set(self.buildings.values()):
            self.study_sites[idx] = dict(tier='general',hours=None,home=-1)
        for idx in self.homes:
            self.study_sites[idx] = dict(tier='residence',hours=(0,1440),home=idx)
        known = config.facility_buildings
        self.unmapped_facilities = []
        for idx in self.activities:
            place = self.places[idx]
            if place.home>=0:
                place.destination = place.home
            elif place.id in known:
                if known[place.id] not in self.buildings:
                    raise ValueError(f'Unknown building {known[place.id]} for {place.id}')
                place.destination = self.buildings[known[place.id]]
            else:
                place.destination = idx
                if place.id in config.standalone_facilities:
                    place.building = place.name
                else:
                    self.unmapped_facilities.append(dict(id=place.id,name=place.name,area=place.building))
            destination = place.destination
            self.places[destination].stops = list(dict.fromkeys(
                self.places[destination].stops+place.stops))
            candidate = dict(tier='library' if place.kind=='study' else 'canteen',hours=place.hours if place.kind=='study' else None,home=-1)
            old = self.study_sites.get(destination)
            ranks = {'library':4,'residence':3,'general':2,'canteen':1}
            if old is None or ranks[candidate['tier']]>ranks[old['tier']]:
                self.study_sites[destination] = candidate
        self.destinations = sorted(set(self.venues.values()) | set(self.homes) | {self.places[i].destination for i in self.activities})
        # Match the mapped precinct buildings rather than inferring area from
        # the nearest bus stop, which can also serve nearby faculty buildings.
        utown_buildings = {'AC','EC','ERC','NAK','RC1','RC2','RC3','SRC','TP','UT','UT22','UTSRC','WC'}
        utown_class_sites = {i for building,i in self.buildings.items()
                             if building in utown_buildings | {'CAPT','RC4','TC','USP','Y'}}
        self.class_destination_zones = {
            i: 'UTOWN' if i in utown_class_sites else self.places[i].building
            for i in set(self.venues.values())
        }
        neutral_facilities = {'ACT:DIN-UTOWN','ACT:DIN-PGP'}
        self.class_distance_exempt = set(self.homes)
        self.class_distance_exempt.update(self.stop_codes[code] for code in ('UTOWN','PGP','PGPR'))
        self.class_distance_exempt.update(i for i in self.destinations if self.places[i].building in utown_buildings)
        self.class_distance_exempt.update(self.places[i].destination for i in self.activities
                                          if self.places[i].id in neutral_facilities)
        self.xy = np.array([p.xy for p in self.places])
        self.distances = distance(self.xy[:,None,:], self.xy[None,:,:])
        for i,p in enumerate(self.places):
            if p.kind == 'stop':
                p.stops = [i]
            if not p.stops:
                p.stops = [int(np.argmin(self.distances[i,:self.stop_count]))]
            first = p.stops[0]
            if len(p.stops)==1 and first in opposite_indices:
                p.stops.append(opposite_indices[first])
        self.facilities = []
        for idx in self.activities:
            place = self.places[idx]
            self.facilities.append(dict(
                id=place.id, name=place.name, activity=place.kind,
                destination=place.destination, building=self.places[place.destination].name,
                xy=self.places[place.destination].xy, source_xy=place.xy,
                candidate_stops=[code for stop in self.places[place.destination].stops for code,value in self.stop_codes.items() if value==stop],
                standalone=place.id in config.standalone_facilities,
                offcampus_food=place.id in ('ACT:DIN-KRMRT','ACT:DIN-SUPPER'),
            ))
        segments = {(a,b) for seq in self.routes.values() for a,b in zip(seq,seq[1:])}
        quantiles = np.quantile([self.distances[a,b] for a,b in segments], [.25,.5,.75])
        self.segment_times = {(a,b): 2 + int(np.sum(self.distances[a,b] > quantiles)) for a,b in segments}
        self.depots = {r:self.stop_codes["KRB" if r in ("A1","A2") else "COM3" if r in ("D1","D2") else "PGP"] for r in self.routes}
        public = "KR-MRT KR-MRT-OPP KRB MUSEUM RAFFLES YIH YIH-OPP CLB IT LT27 S17 UHALL-OPP UHC UHC-OPP JP-SCH-16151"
        self.gateways = [self.stop_codes[s] for s in public.split()]
        mrt_gateways = {self.stop_codes[code] for code in ('KR-MRT','KR-MRT-OPP')}
        self.gateway_weights = np.array([config.mrt_gateway_weight if g in mrt_gateways else 1. for g in self.gateways])
        self.warnings = ["Regular teaching dates use configured instructional Week 1 anchors and exclude holidays and recess. Explicit class date ranges apply only inside the teaching calendar.", "Gateway list uses mapped public-connected shuttle stops. Unmapped public stops await coordinates.", "UTR uses a UTown anchor. Inherited residence coordinates, including Eusoff Hall, remain unverified.", "Techno Edge uses previously requested IT/CLB stops.", "Route geometry uses straight segments between supplied stop coordinates.", "K downstream timing derives from segment times and does not match the published 16-minute Kent Vale offset."]

    def teaching_dates(self):
        dates = {}
        excluded = set(self.config.excluded_teaching_dates)
        for semester, start in enumerate(self.config.semester_starts, 1):
            origin = date.fromisoformat(start)
            for week in range(1,14):
                for weekday in range(5):
                    day = (origin + timedelta(days=7*(week-1+(week>6))+weekday)).isoformat()
                    if day not in excluded:
                        dates[day] = (semester, week, weekday)
        return dates

    def days(self):
        return sorted(self.teaching_dates())

    def sessions(self, day):
        if day in self._sessions_by_day:
            return self._sessions_by_day[day]
        current = date.fromisoformat(day)
        sessions, excluded = [], []
        term = self.teaching_dates().get(day)
        if term is None:
            raise ValueError(f"{day} is not a configured instructional weekday")
        semester,week,_ = term
        for row_id, row in enumerate(self.class_rows, 2):
            if row[4] != current.strftime("%A"):
                continue
            weeks = str(row[7])
            if " to " in weeks:
                a,b = weeks.split(" to ")
                applies = a <= day <= b
            else:
                applies = int(row[0])==semester and week in [int(v) for v in re.findall(r"\d+", weeks)]
            if not applies:
                continue
            venue = str(row[8])
            if "hybrid" in venue.lower():
                excluded.append([row_id,"e-learning"])
                continue
            if venue not in self.venues:
                excluded.append([row_id,"unmapped venue",venue])
                continue
            target = float(row[9] or 0) * float(row[11] or 0)
            if target <= 0:
                continue
            start,end = minute(row[5]),minute(row[6])
            if end <= start:
                raise ValueError(f"Invalid duration row {row_id}")
            sessions.append(Session(len(sessions), self.venues[venue], start,end,target,row_id))
        self._sessions_by_day[day] = sessions,excluded
        return sessions, excluded

