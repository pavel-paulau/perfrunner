import glob
from collections import defaultdict, namedtuple
from unittest import TestCase

from perfrunner.settings import ClusterSpec, TestConfig
from perfrunner.workloads.tcmalloc import KeyValueIterator, LargeIterator
from spring import docgen


class SettingsTest(TestCase):

    def test_stale_update_after(self):
        test_config = TestConfig()
        test_config.parse('tests/query_lat_20M_basic.test')
        query_params = test_config.access_settings.query_params
        self.assertEqual(query_params, {'stale': 'false'})

    def test_cluster_specs(self):
        for file_name in glob.glob("clusters/*.spec"):
            cluster_spec = ClusterSpec()
            cluster_spec.parse(file_name, override=None)

    def test_override(self):
        test_config = TestConfig()
        test_config.parse('tests/query_lat_20M_basic.test',
                          override='cluster.mem_quota.5555')
        self.assertEqual(test_config.cluster.mem_quota, 5555)

    def test_soe_backup_repo(self):
        for file_name in glob.glob("tests/soe/*.test"):
            test_config = TestConfig()
            test_config.parse(file_name)
            self.assertNotEqual(test_config.restore_settings.backup_repo, '')


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


WorkloadSettings = namedtuple('WorkloadSettings', ('items',
                                                   'workers',
                                                   'working_set',
                                                   'working_set_access',
                                                   'key_fmtr'))


class SpringTest(TestCase):

    def test_seq_key_generator(self):
        ws = WorkloadSettings(items=10 ** 5, workers=25, working_set=100,
                              working_set_access=100, key_fmtr='decimal')

        keys = []
        for worker in range(ws.workers):
            generator = docgen.SequentialKey(worker, ws, prefix='test')
            keys += [key.string for key in generator]

        expected = [docgen.Key(number=i, prefix='test', fmtr='decimal').string
                    for i in range(ws.items)]

        self.assertEqual(sorted(keys), expected)

    def test_unordered_key_generator(self):
        ws = WorkloadSettings(items=10 ** 5, workers=25, working_set=100,
                              working_set_access=100, key_fmtr='decimal')

        keys = []
        for worker in range(ws.workers):
            generator = docgen.UnorderedKey(worker, ws, prefix='test')
            keys += [key.string for key in generator]

        expected = [docgen.Key(number=i, prefix='test', fmtr='decimal').string
                    for i in range(ws.items)]

        self.assertEqual(sorted(keys), expected)

    def test_kew_ordered_keys(self):
        ws = WorkloadSettings(items=10 ** 4, workers=40, working_set=10,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.SequentialKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key)

        key_gen = docgen.NewOrderedKey(prefix='test', fmtr='decimal')
        for op in range(1, 10 ** 3):
            key = key_gen.next(ws.items + op)
            self.assertNotIn(key, keys)

    def test_zipf_generator(self):
        ws = WorkloadSettings(items=10 ** 3, workers=40, working_set=10,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                self.assertNotIn(key, keys)
                keys.add(key.string)
        self.assertEqual(len(keys), ws.items)

        key_gen = docgen.ZipfKey(prefix='test', fmtr='decimal')
        for op in range(10 ** 4):
            key = key_gen.next(curr_deletes=100, curr_items=ws.items)
            self.assertIn(key.string, keys)

    def doc_generators(self, size: int):
        for dg in (
            docgen.ReverseLookupDocument(avg_size=size, prefix='n1ql'),
            docgen.ReverseRangeLookupDocument(avg_size=size, prefix='n1ql',
                                              range_distance=100),
            docgen.ExtReverseLookupDocument(avg_size=size, prefix='n1ql',
                                            num_docs=10 ** 6),
            docgen.ArrayIndexingDocument(avg_size=size, prefix='n1ql',
                                         array_size=10, num_docs=10 ** 6),
            docgen.ProfileDocument(avg_size=size, prefix='n1ql'),
            docgen.String(avg_size=size)
        ):
            yield dg

    def test_doc_size(self):
        size = 1024
        key_gen = docgen.NewOrderedKey(prefix='n1ql', fmtr='decimal')

        for dg in self.doc_generators(size=size):
            for i in range(10 ** 4):
                key = key_gen.next(i)
                doc = dg.next(key=key)
                actual_size = len(str(doc))
                self.assertAlmostEqual(actual_size, size,
                                       delta=size * 0.05,  # 5% variation
                                       msg=dg.__class__.__name__)

    def test_doc_size_variation(self):
        size = 512
        key_gen = docgen.NewOrderedKey(prefix='test', fmtr='decimal')
        doc_gen = docgen.Document(avg_size=size)

        for i in range(10 ** 4):
            key = key_gen.next(i)
            doc = doc_gen.next(key=key)
            actual_size = len(str(doc))
            self.assertAlmostEqual(actual_size, size,
                                   delta=size * doc_gen.SIZE_VARIATION)

    def test_small_documents(self):
        key_gen = docgen.NewOrderedKey(prefix='test', fmtr='decimal')
        doc_gen = docgen.Document(avg_size=150)

        for i in range(10 ** 3):
            key = key_gen.next(i)
            doc = doc_gen.next(key=key)
            size = len(str(doc))

            self.assertEqual(doc["body"], "")
            self.assertAlmostEqual(size, doc_gen.OVERHEAD, delta=20)

    def test_hot_keys(self):
        ws = WorkloadSettings(items=10 ** 4, workers=40, working_set=10,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                self.assertNotIn(key.string, keys)
                keys.add(key.string)
        self.assertEqual(len(keys), ws.items)

        hot_keys = set()
        for worker in range(ws.workers):
            for key in docgen.HotKey(sid=worker, ws=ws, prefix='test'):
                self.assertNotIn(key.string, hot_keys)
                self.assertIn(key.string, keys)
                hot_keys.add(key.string)
        self.assertEqual(len(hot_keys), ws.working_set * ws.items // 100)

    def test_uniform_keys(self):
        ws = WorkloadSettings(items=10 ** 3, workers=10, working_set=100,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key.string)

        key_gen = docgen.UniformKey(prefix='test',
                                    fmtr='decimal')
        for op in range(10 ** 4):
            key = key_gen.next(curr_items=ws.items, curr_deletes=100)
            self.assertIn(key.string, keys)

    def test_working_set_keys(self):
        ws = WorkloadSettings(items=10 ** 3, workers=10, working_set=90,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key.string)

        key_gen = docgen.WorkingSetKey(prefix='test',
                                       working_set=ws.working_set,
                                       working_set_access=50,
                                       fmtr='decimal')
        for op in range(10 ** 4):
            key = key_gen.next(curr_items=ws.items, curr_deletes=100,
                               curr_offset=200)
            self.assertIn(key.string, keys)

    def test_cas_updates(self):
        ws = WorkloadSettings(items=10 ** 3, workers=20, working_set=100,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key.string)

        cases = defaultdict(set)
        key_gen = docgen.KeyForCASUpdate(total_workers=ws.workers, prefix='test',
                                         fmtr='decimal')
        for sid in 5, 6:
            for op in range(10 ** 3):
                key = key_gen.next(sid=sid, curr_items=ws.items)
                self.assertIn(key.string, keys)
                cases[sid].add(key.string)
        self.assertEqual(cases[5] & cases[6], set())

    def test_key_for_removal(self):
        ws = WorkloadSettings(items=10 ** 3, workers=20, working_set=100,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key.string)

        key_gen = docgen.KeyForRemoval(prefix='test', fmtr='decimal')
        for op in range(1, 100):
            key = key_gen.next(op)
            self.assertIn(key.string, keys)

    def test_keys_without_prefix(self):
        ws = WorkloadSettings(items=10 ** 3, workers=20, working_set=100,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix=''):
                keys.add(key.string)

        expected = [docgen.Key(number=i, prefix='', fmtr='decimal').string
                    for i in range(ws.items)]

        self.assertEqual(sorted(keys), expected)

    def test_working_set_hits(self):
        ws = WorkloadSettings(items=10 ** 3, workers=40, working_set=20,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key.string)
        keys = sorted(keys)

        hot_keys = set()
        for worker in range(ws.workers):
            for key in docgen.HotKey(sid=worker, ws=ws, prefix='test'):
                hot_keys.add(key.string)
        hot_keys = sorted(hot_keys)

        wsk = docgen.WorkingSetKey(working_set=ws.working_set,
                                   working_set_access=ws.working_set_access,
                                   prefix='test',
                                   fmtr='decimal')
        for op in range(10 ** 5):
            key = wsk.next(curr_items=ws.items, curr_deletes=0, curr_offset=0)
            self.assertIn(key.string, keys)
            if key.hit:
                self.assertIn(key.string, hot_keys)
            else:
                self.assertNotIn(key.string, hot_keys)

    def test_working_set_drift(self):
        ws = WorkloadSettings(items=10 ** 3, workers=40, working_set=20,
                              working_set_access=100, key_fmtr='decimal')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                keys.add(key.string)

        hot_keys = set()
        for worker in range(ws.workers):
            for key in docgen.HotKey(sid=worker, ws=ws, prefix='test'):
                hot_keys.add(key.string)

        wsk = docgen.WorkingSetKey(working_set=ws.working_set,
                                   working_set_access=ws.working_set_access,
                                   prefix='test',
                                   fmtr='decimal')
        drifted_hot_keys = set()
        drift = 100
        for op in range(10 ** 5):
            key = wsk.next(curr_items=ws.items, curr_deletes=0,
                           curr_offset=drift)
            self.assertIn(key.string, keys)
            if key.hit:
                drifted_hot_keys.add(key.string)

        delta = drifted_hot_keys - hot_keys
        self.assertEqual(len(delta), drift)

    def test_hash_fmtr(self):
        ws = WorkloadSettings(items=10 ** 3, workers=40, working_set=20,
                              working_set_access=100, key_fmtr='hash')

        keys = set()
        for worker in range(ws.workers):
            for key in docgen.UnorderedKey(sid=worker, ws=ws, prefix='test'):
                self.assertNotIn(key.string, keys)
                keys.add(key.string)
