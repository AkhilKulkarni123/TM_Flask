class SlitherRushSimulation:
    def __init__(self, manager):
        self.m = manager

    def step(self, now: float, dt: float) -> None:
        self.m.tick(now, dt)
