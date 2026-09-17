"""A minimal broker and scheduler with decorator registration."""


class Broker:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, topic):
        def register(fn):
            self.handlers[topic] = fn
            return fn

        return register


class Scheduler:
    def __init__(self):
        self.jobs = []

    def every(self, interval):
        def register(fn):
            self.jobs.append((interval, fn))
            return fn

        return register


broker = Broker()
scheduler = Scheduler()
