import glob
from collections import namedtuple
from unittest import TestCase

from perfrunner.helpers.misc import server_group
from perfrunner.settings import ClusterSpec, TestConfig
from perfrunner.workloads.tcmalloc import KeyValueIterator, LargeIterator
from spring.docgen import Document, SequentialKey, UnorderedKey, ZipfKey
from spring.wgen import Worker


class RebalanceTests(TestCase):

    def test_server_group_6_to_8(self):
        servers = range(8)
        initial_nodes = 6
        nodes_after = 8
        group_number = 3

        groups = []
        for i, host in enumerate(servers[initial_nodes:nodes_after],
                                 start=initial_nodes):
            g = server_group(servers[:nodes_after], group_number, i)
            groups.append(g)
        self.assertEqual(groups, ['Group 3', 'Group 3'])

    def test_initial_4_out_of_8(self):
        servers = range(8)
        initial_nodes = 4
        group_number = 2

        groups = []
        for i, host in enumerate(servers[1:initial_nodes], start=1):
            g = server_group(servers[:initial_nodes], group_number, i)
            groups.append(g)
        self.assertEqual(groups, ['Group 1', 'Group 2', 'Group 2'])

    def test_server_group_3_to_4(self):
        servers = range(8)
        initial_nodes = 3
        nodes_after = 4
        group_number = 2

        groups = []
        for i, host in enumerate(servers[initial_nodes:nodes_after],
                                 start=initial_nodes):
            g = server_group(servers[:nodes_after], group_number, i)
            groups.append(g)
        self.assertEqual(groups, ['Group 2'])


class SettingsTest(TestCase):

    def test_stale_update_after(self):
        test_config = TestConfig()
        test_config.parse('tests/query_lat_20M_basic.test', [])
        query_params = test_config.access_settings.query_params
        self.assertEqual(query_params, {'stale': 'false'})

    def test_cluster_specs(self):
        for file_name in glob.glob("clusters/*.spec"):
            cluster_spec = ClusterSpec()
            cluster_spec.parse(file_name, override=())


class WorkloadTest(TestCase):

    def test_value_size(self):
        for _ in range(100):
            iterator = KeyValueIterator(10000)
            batch = iterator.next()
            values = [len(str(v)) for k, v in batch]
            mean = sum(values) / len(values)
            self.assertAlmostEqual(mean, 1024, delta=128)

    def test_large_field_size(self):
        field = LargeIterator()._field('000000000001')
        size = len(str(field))
        self.assertAlmostEqual(size, LargeIterator.FIELD_SIZE, delta=16)


SpringSettings = namedtuple('SprintSettings', ('items', 'workers'))


class SpringTest(TestCase):

    def test_spring_imports(self):
        self.assertEqual(Document.SIZE_VARIATION, 0.25)
        self.assertEqual(Worker.BATCH_SIZE, 100)

    def test_seq_key_generator(self):
        settings = SpringSettings(items=10 ** 5, workers=25)

        keys = []
        for worker in range(settings.workers):
            generator = SequentialKey(worker, settings, prefix='test')
            keys += [key for key in generator]

        expected_keys = ['test-%012d' % i for i in range(1, settings.items + 1)]

        self.assertEqual(sorted(keys), expected_keys)

    def test_unordered_key_generator(self):
        settings = SpringSettings(items=10 ** 5, workers=25)

        keys = []
        for worker in range(settings.workers):
            generator = UnorderedKey(worker, settings, prefix='test')
            keys += [key for key in generator]

        expected_keys = ['test-%012d' % i for i in range(1, settings.items + 1)]

        self.assertEqual(sorted(keys), expected_keys)

    def test_zipf_generator(self):
        num_items = 10 ** 4
        generator = ZipfKey(prefix='')

        for i in range(10 ** 5):
            key = generator.next(curr_deletes=0, curr_items=num_items)
            key = int(key)
            self.assertLessEqual(key, num_items)
            self.assertGreaterEqual(key, 1)
