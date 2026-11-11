"""User-configured rule dispatcher; the original BASE1 is left unchanged."""
from .protocol import NetworkRule, history


class CustomRule(NetworkRule):
    def __init__(self, crowd_level='HIGH', share=.7, minimum_buses=2):
        super().__init__()
        self.crowd_level = crowd_level
        self.share = share
        self.minimum_buses = minimum_buses

    def decide(self, sim):
        levels = {'LOW':0,'MEDIUM':1,'HIGH':2}
        runs = {route:{} for route in sim.data.routes}
        for snapshot in history(sim,30):
            for bus in snapshot['vehicles']:
                values = runs[bus['route_id']]
                qualifies = levels[bus['crowd_level']] >= levels[self.crowd_level]
                values[bus['run_id']] = values.get(bus['run_id'],False) or qualifies
        pending = {b['route_id'] for b in sim.observation()['extra_bus_commitments']
                   if b['status']=='en_route_from_depot'}
        selected, self.details = [], []
        for route,values in runs.items():
            total, crowded = len(values),sum(values.values())
            share = crowded/total if total else None
            congested = total>=self.minimum_buses and share is not None and share>=self.share
            self.details.append(dict(route=route,total=total,crowded=crowded,share=share,
                                     pending=route in pending,congested=congested))
            if congested and route not in pending:
                selected.append(route)
        for route in selected:
            self.last[route] = sim.t
        return dict(routes=sorted(selected))
