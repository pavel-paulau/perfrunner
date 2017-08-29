from time import sleep, time

from cbagent.collectors import Latency, ObserveIndexLatency
from logger import logger
from perfrunner.helpers.misc import uhex
from spring.docgen import Document


class DurabilityLatency(ObserveIndexLatency, Latency):

    COLLECTOR = "durability"

    METRICS = "latency_replicate_to", "latency_persist_to"

    DURABILITY_TIMEOUT = 120

    def __init__(self, settings, workload):
        super().__init__(settings)

        self.new_docs = Document(workload.size)

        self.pools = self.init_pool(settings)

    def endure(self, pool, metric):
        client = pool.get_client()

        key = uhex()
        doc = self.new_docs.next(key)

        t0 = time()

        client.upsert(key, doc)
        if metric == "latency_persist_to":
            client.endure(key, persist_to=1, replicate_to=0, interval=0.010,
                          timeout=120)
        else:
            client.endure(key, persist_to=0, replicate_to=1, interval=0.001)

        latency = 1000 * (time() - t0)  # Latency in ms

        sleep_time = max(0, self.MAX_POLLING_INTERVAL - latency)

        client.delete(key)
        pool.release_client(client)
        return {metric: latency}, sleep_time

    def sample(self):
        while True:
            for bucket, pool in self.pools:
                for metric in self.METRICS:
                    try:
                        stats, sleep_time = self.endure(pool, metric)
                        self.store.append(stats,
                                          cluster=self.cluster,
                                          bucket=bucket,
                                          collector=self.COLLECTOR)
                        sleep(sleep_time)
                    except Exception as e:
                        logger.warn(e)

    def collect(self):
        ObserveIndexLatency.collect(self)
