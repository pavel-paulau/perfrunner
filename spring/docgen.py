import math
import random
import time
from hashlib import md5
from typing import Iterator, List, Tuple

import numpy as np
from fastdocgen import build_achievements

from spring.dictionary import (
    NUM_STATES,
    NUM_STREET_SUFFIXES,
    STATES,
    STREET_SUFFIX,
)
from spring.settings import WorkloadSettings

PRIME = 971


def decimal_fmtr(key: int, prefix: str) -> str:
    key = '%012d' % key
    if prefix:
        return '%s-%s' % (prefix, key)
    return key


class Key:

    def __init__(self, number: int, prefix: str, fmtr: str, hit: bool = False):
        self.number = number
        self.prefix = prefix
        self.hit = hit
        self.fmtr = fmtr

    @property
    def string(self) -> str:
        key = decimal_fmtr(self.number, self.prefix)
        if self.fmtr == 'hash':
            return md5(key.encode('utf-8')).hexdigest()
        return key


class NewOrderedKey:

    """Generate ordered keys with an optional common prefix.

    These keys are usually used for inserting new documents into the database.

    Example: "38d7cd-000072438963"

    The suffix is a 12 characters long string consisting of digits from 0 to 9.

    This key pattern is rather uncommon in real-world scenarios.
    """

    def __init__(self, prefix: str, fmtr: str):
        self.prefix = prefix
        self.fmtr = fmtr

    def next(self, curr_items: int) -> Key:
        return Key(number=curr_items, prefix=self.prefix, fmtr=self.fmtr)


class KeyForRemoval:

    """Pick an existing key at the beginning of the key space."""

    def __init__(self, prefix: str, fmtr: str):
        self.prefix = prefix
        self.fmtr = fmtr

    def next(self, curr_deletes: int) -> Key:
        return Key(number=curr_deletes, prefix=self.prefix, fmtr=self.fmtr)


class UniformKey:

    """Randomly sample an existing key from the entire key space.

    Sampling uses discrete uniform distribution.

    |<-------------------- key space -------------------->|

    |xxxxxxxxx|...........................................|

              ^                                           ^
              |                                           |

          curr_deletes                                curr_items

    This generator should not be used when the key access pattern is important.
    """

    def __init__(self, prefix: str, fmtr: str):
        self.prefix = prefix
        self.fmtr = fmtr

    def next(self, curr_items: int, curr_deletes: int, *args) -> Key:
        number = np.random.random_integers(low=curr_deletes,
                                           high=curr_items - 1)
        return Key(number=number, prefix=self.prefix, fmtr=self.fmtr)


class WorkingSetKey:

    """Extend UniformKey by sampling keys from the fixed working set.

    Working set is a subset of the entire key space.

    There are two options that characterize the working set:
    * working_set - a percentage (from 0 to 100) of the entire key space that
    should be considered as the working set.
    * working_set_access - a percentage (from 0 to 100) that defines the
    probability at which the keys from the working set are being used. This
    parameter implements deterministic cache miss ratio.

    |<--------------------------- key space ------------------------->|

            |<----------- cold items ---------->|<---- hot items ---->|

    |xxxxxxx|.........................................................|

            ^                                                         ^
            |                                                         |

        curr_deletes                                              curr_items
    """

    def __init__(self, working_set: int, working_set_access: int, prefix: str,
                 fmtr: str):
        self.working_set = working_set
        self.working_set_access = working_set_access
        self.prefix = prefix
        self.fmtr = fmtr

    def next(self, curr_items: int, curr_deletes: int, curr_offset: int) -> Key:
        num_existing_items = curr_items - curr_deletes
        num_hot_items = int(num_existing_items * self.working_set / 100)
        num_cold_items = num_existing_items - num_hot_items

        left_boundary = curr_deletes
        if random.randint(0, 100) <= self.working_set_access:  # cache hit
            hit = True
            left_boundary += num_cold_items
            right_boundary = curr_items
        else:  # cache miss
            hit = False
            right_boundary = left_boundary + num_cold_items

        number = np.random.random_integers(low=left_boundary,
                                           high=right_boundary - 1)
        number += curr_offset
        number = (number * PRIME) % num_existing_items
        return Key(number=number, prefix=self.prefix, fmtr=self.fmtr, hit=hit)


class ZipfKey:

    ALPHA = 1.9

    def __init__(self, prefix: str, fmtr: str):
        self.prefix = prefix
        self.fmtr = fmtr

    def next(self, curr_items: int, curr_deletes: int, *args) -> Key:
        number = curr_items - np.random.zipf(a=self.ALPHA)
        if number <= curr_deletes:
            number = curr_items - 1
        return Key(number=number, prefix=self.prefix, fmtr=self.fmtr)


class SequentialKey:

    """Sequentially generate new keys equally divided the workers.

    SequentialKey equally divides the key space between the workers and
    sequentially iterates over a given part of the key space (based on the
    sequential worker identifier).

    This generator is used for loading data.
    """

    def __init__(self, sid: int, ws: WorkloadSettings, prefix: str):
        self.sid = sid
        self.ws = ws
        self.prefix = prefix

    def __iter__(self) -> Iterator[Key]:
        for seq_id in range(self.sid, self.ws.items, self.ws.workers):
            yield Key(number=seq_id, prefix=self.prefix, fmtr=self.ws.key_fmtr)


class HotKey:

    """Generate the existing keys equally divided between the workers.

    HotKey equally divides the working set between the workers and iterates over
    a given part of the working set (based on the sequential worker identifier).

    This generator is used for warming up the working set.
    """

    def __init__(self, sid: int, ws: WorkloadSettings, prefix: str):
        self.sid = sid
        self.ws = ws
        self.prefix = prefix

    def __iter__(self) -> Iterator[Key]:
        num_hot_keys = int(self.ws.items * self.ws.working_set / 100)
        num_cold_items = self.ws.items - num_hot_keys

        for seq_id in range(num_cold_items + self.sid,
                            self.ws.items,
                            self.ws.workers):
            seq_id = (seq_id * PRIME) % self.ws.items
            yield Key(number=seq_id, prefix=self.prefix, fmtr=self.ws.key_fmtr)


class UnorderedKey:

    """Improve SequentialKey by randomizing the order of insertions.

    The key space is still the same.
    """

    def __init__(self, sid, ws: WorkloadSettings, prefix):
        self.sid = sid
        self.ws = ws
        self.prefix = prefix

    def __iter__(self) -> Iterator[Key]:
        keys_per_workers = self.ws.items // self.ws.workers

        number = 0
        for _ in range(keys_per_workers):
            number = (number + PRIME) % keys_per_workers
            number = number + self.sid * keys_per_workers
            yield Key(number=number, prefix=self.prefix, fmtr=self.ws.key_fmtr)


class KeyForCASUpdate:

    def __init__(self, total_workers: int, prefix: str, fmtr: str):
        self.n1ql_workers = total_workers
        self.prefix = prefix
        self.fmtr = fmtr

    def next(self, sid: int, curr_items: int) -> Key:
        per_worker_items = curr_items // self.n1ql_workers

        left_boundary = sid * per_worker_items
        right_boundary = left_boundary + per_worker_items

        number = np.random.random_integers(low=left_boundary,
                                           high=right_boundary - 1)
        return Key(number=number, prefix=self.prefix, fmtr=self.fmtr)


class FTSKey:

    def __init__(self, ws: WorkloadSettings):
        self.mutate_items = 0
        if ws.fts_config:
            self.mutate_items = ws.fts_config.mutate_items

    def next(self) -> str:
        return hex(random.randint(0, self.mutate_items))[2:]


class String:

    def __init__(self, avg_size: int):
        self.avg_size = avg_size

    @staticmethod
    def _build_alphabet(key: str) -> str:
        _key = key.encode('utf-8')
        return md5(_key).hexdigest() + md5(_key[::-1]).hexdigest()

    @staticmethod
    def _build_string(alphabet: str, length: float) -> str:
        length_int = int(length)
        num_slices = int(math.ceil(length / 64))  # 64 == len(alphabet)
        body = num_slices * alphabet
        return body[:length_int]

    def next(self, key: Key) -> str:
        alphabet = self._build_alphabet(key.string)

        return self._build_string(alphabet, self.avg_size)


class Document(String):

    SIZE_VARIATION = 0.25  # 25%

    OVERHEAD = 205  # Minimum size due to static fields, body size is variable

    @classmethod
    def _get_variation_coeff(cls) -> float:
        return np.random.uniform(1 - cls.SIZE_VARIATION, 1 + cls.SIZE_VARIATION)

    @staticmethod
    def _build_name(alphabet: str) -> str:
        return '%s %s' % (alphabet[:6], alphabet[6:12])  # % is faster than format()

    @staticmethod
    def _build_email(alphabet: str) -> str:
        return '%s@%s.com' % (alphabet[12:18], alphabet[18:24])

    @staticmethod
    def _build_alt_email(alphabet: str) -> str:
        name = random.randint(1, 9)
        domain = random.randint(12, 18)
        return '%s@%s.com' % (alphabet[name:name + 6], alphabet[domain:domain + 6])

    @staticmethod
    def _build_city(alphabet: str) -> str:
        return alphabet[24:30]

    @staticmethod
    def _build_realm(alphabet: str) -> str:
        return alphabet[30:36]

    @staticmethod
    def _build_country(alphabet: str) -> str:
        return alphabet[42:48]

    @staticmethod
    def _build_county(alphabet: str) -> str:
        return alphabet[48:54]

    @staticmethod
    def _build_street(alphabet: str) -> str:
        return alphabet[54:62]

    @staticmethod
    def _build_coins(alphabet: str) -> float:
        return max(0.1, int(alphabet[36:40], 16) / 100)

    @staticmethod
    def _build_gmtime(alphabet: str) -> Tuple[int]:
        seconds = 396 * 24 * 3600 * (int(alphabet[63], 16) % 12)
        return tuple(time.gmtime(seconds))

    @staticmethod
    def _build_year(alphabet: str) -> int:
        return 1985 + int(alphabet[62], 16)

    @staticmethod
    def _build_state(alphabet: str) -> str:
        idx = alphabet.find('7') % NUM_STATES
        return STATES[idx][0]

    @staticmethod
    def _build_full_state(alphabet: str) -> str:
        idx = alphabet.find('8') % NUM_STATES
        return STATES[idx][1]

    @staticmethod
    def _build_category(alphabet: str) -> int:
        return int(alphabet[41], 16) % 3

    @staticmethod
    def _build_achievements(alphabet: str) -> List[int]:
        return build_achievements(alphabet) or [0]

    def _size(self) -> float:
        if self.avg_size <= self.OVERHEAD:
            return 0
        return self._get_variation_coeff() * (self.avg_size - self.OVERHEAD)

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': self._build_name(alphabet),
            'email': self._build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'city': self._build_city(alphabet),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'body': self._build_string(alphabet, size),
        }


class NestedDocument(Document):

    OVERHEAD = 450  # Minimum size due to static fields, body size is variable

    def __init__(self, avg_size: int):
        super().__init__(avg_size)
        self.capped_field_value = {}  # type: dict

    def _size(self) -> float:
        if self.avg_size <= self.OVERHEAD:
            return 0
        if random.random() < 0.975:  # Normal distribution, mean=self.avg_size
            normal = np.random.normal(loc=1.0, scale=0.17)
            return (self.avg_size - self.OVERHEAD) * normal
        else:  # Outliers - beta distribution, 2KB-2MB range
            return 2048 / np.random.beta(a=2.2, b=1.0)

    def next(self, key: Key):
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': {'f': {'f': {'f': self._build_name(alphabet)}}},
            'email': {'f': {'f': self._build_email(alphabet)}},
            'alt_email': {'f': {'f': self._build_alt_email(alphabet)}},
            'street': {'f': {'f': self._build_street(alphabet)}},
            'city': {'f': {'f': self._build_city(alphabet)}},
            'county': {'f': {'f': self._build_county(alphabet)}},
            'state': {'f': self._build_state(alphabet)},
            'full_state': {'f': self._build_full_state(alphabet)},
            'country': {'f': self._build_country(alphabet)},
            'realm': {'f': self._build_realm(alphabet)},
            'coins': {'f': self._build_coins(alphabet)},
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'gmtime': self._build_gmtime(alphabet),
            'year': self._build_year(alphabet),
            'body': self._build_string(alphabet, size),
        }


class LargeDocument(NestedDocument):

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        return {
            'nest1': super().next(key),
            'nest2': super(NestedDocument, self).next(key),
            'name': self._build_name(alphabet),
            'email': self._build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'city': self._build_city(alphabet),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
        }


class ReverseLookupDocument(NestedDocument):

    OVERHEAD = 420

    def _size(self) -> float:
        return self.avg_size - self.OVERHEAD

    def __init__(self, avg_size: int, prefix: str):
        super().__init__(avg_size)
        self.prefix = prefix
        self.is_random = prefix != 'n1ql'

    def build_email(self, alphabet: str) -> str:
        if self.is_random:
            return self._build_alt_email(alphabet)
        else:
            return self._build_email(alphabet)

    def _build_capped(self, alphabet: str, seq_id: int, num_unique: int) -> str:
        if self.is_random:
            offset = random.randint(1, 9)
            return '%s' % alphabet[offset:offset + 6]

        index = seq_id // num_unique
        return '%s_%s_%s' % (self.prefix, num_unique, index)

    def _build_topics(self, seq_id: int) -> List[str]:
        return []

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': self._build_name(alphabet),
            'email': self.build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'street': self._build_street(alphabet),
            'city': self._build_city(alphabet),
            'county': self._build_county(alphabet),
            'state': self._build_state(alphabet),
            'full_state': self._build_full_state(alphabet),
            'country': self._build_country(alphabet),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'gmtime': self._build_gmtime(alphabet),
            'year': self._build_year(alphabet),
            'body': self._build_string(alphabet, size),
            'capped_small': self._build_capped(alphabet, key.number, 100),
            'topics': self._build_topics(key.number),
        }


class ReverseRangeLookupDocument(ReverseLookupDocument):

    OVERHEAD = 480

    def __init__(self, avg_size: int, prefix: str, range_distance: int):
        super().__init__(avg_size, prefix)
        if self.prefix is None:
            self.prefix = ""
        # Keep one extra as query runs from greater than 'x' to less than 'y'
        # both exclusive.
        self.distance = range_distance + 1

    def _build_capped(self, alphabet: str, seq_id: int, num_unique: int) -> str:
        if self.is_random:
            offset = random.randint(1, 9)
            return '%s' % alphabet[offset:offset + 6]

        index = seq_id // num_unique
        return '%s_%s_%12s' % (self.prefix, num_unique, index)

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': self._build_name(alphabet),
            'email': self.build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'street': self._build_street(alphabet),
            'city': self._build_city(alphabet),
            'county': self._build_county(alphabet),
            'state': self._build_state(alphabet),
            'full_state': self._build_full_state(alphabet),
            'country': self._build_country(alphabet),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'gmtime': self._build_gmtime(alphabet),
            'year': self._build_year(alphabet),
            'body': self._build_string(alphabet, size),
            'capped_small': self._build_capped(alphabet, key.number, 100),
            'capped_small_range': self._build_capped(alphabet,
                                                     key.number + (self.distance * 100),
                                                     100),
            'topics': self._build_topics(key.number),
        }


class ExtReverseLookupDocument(ReverseLookupDocument):

    OVERHEAD = 500

    def __init__(self, avg_size: int, prefix: str, num_docs: int):
        super().__init__(avg_size, prefix)
        self.num_docs = num_docs

    def _build_topics(self, seq_id: int) -> List[str]:
        """1:4 reference to JoinedDocument keys."""
        return [
            decimal_fmtr((seq_id + 11) % self.num_docs, self.prefix),
            decimal_fmtr((seq_id + 19) % self.num_docs, self.prefix),
            decimal_fmtr((seq_id + 23) % self.num_docs, self.prefix),
            decimal_fmtr((seq_id + 29) % self.num_docs, self.prefix),
        ]


class JoinedDocument(ReverseLookupDocument):

    def __init__(self, avg_size: int, prefix: str, num_docs: int,
                 num_categories: int, num_replies: int):
        super().__init__(avg_size, prefix)
        self.num_categories = num_categories
        self.num_docs = num_docs
        self.num_replies = num_replies

    def _build_owner(self, seq_id: int) -> str:
        """4:1 reference to ReverseLookupDocument keys."""
        ref_id = seq_id % (self.num_docs // 4)
        return decimal_fmtr(ref_id, self.prefix)

    def _build_title(self, alphabet: str) -> str:
        return alphabet[:32]

    def _build_categories(self, seq_id: int) -> List[str]:
        """1:4 reference to RefDocument keys."""
        return [
            decimal_fmtr((seq_id + 11) % self.num_categories, self.prefix),
            decimal_fmtr((seq_id + 19) % self.num_categories, self.prefix),
            decimal_fmtr((seq_id + 23) % self.num_categories, self.prefix),
            decimal_fmtr((seq_id + 29) % self.num_categories, self.prefix),
        ]

    def _build_user(self, seq_id: int, idx: int) -> str:
        return decimal_fmtr((seq_id + idx + 537) % self.num_docs, self.prefix)

    def _build_replies(self, seq_id: int) -> List[dict]:
        """1:N references to ReverseLookupDocument keys."""
        return [
            {'user': self._build_user(seq_id, idx)}
            for idx in range(self.num_replies)
        ]

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)

        return {
            'owner': self._build_owner(key.number),
            'title': self._build_title(alphabet),
            'capped': self._build_capped(alphabet, key.number, 100),
            'categories': self._build_categories(key.number),
            'replies': self._build_replies(key.number),
        }


class RefDocument(ReverseLookupDocument):

    def _build_ref_name(self, seq_id: int) -> str:
        return decimal_fmtr(seq_id, self.prefix)

    def next(self, key: Key) -> dict:
        return {
            'name': self._build_ref_name(key.number),
        }


class ArrayIndexingDocument(ReverseLookupDocument):

    """Extend ReverseLookupDocument by adding two new fields.

    achievements1 is a variable-length array (default length is 10). Every
    instance of achievements1 is unique. This field is useful for single lookups.

    achievements2 is a fixed-length array. Each instance of achievements2 is
    repeated 100 times (ARRAY_CAP). This field is useful for range queries.
    """

    ARRAY_CAP = 100

    ARRAY_SIZE = 10

    OVERHEAD = 530

    def __init__(self, avg_size: int, prefix: str, array_size: int, num_docs: int):
        super().__init__(avg_size, prefix)
        self.array_size = array_size
        self.num_docs = num_docs

    def _build_achievements1(self, seq_id: int) -> List[int]:
        """Build an array of integers.

        Every document reserves a range of numbers that can be used for a
        new array.

        The left side of range is always based on sequential document ID.

        Random arrays make a few additional steps:
        * The range is shifted by the total number of documents so that static (
        non-random) and random documents do not overlap.
        * The range is doubled so that it's possible vary elements in a new
        array.
        * The left side of range is randomly shifted.

        Here is an example of a new random array for seq_id=7, total 100
        documents and 10 elements in array:
            1) offset is set to 1000.
            2) offset is incremented by 140.
            3) offset is incremented by a random number (e.g., 5).
            4) [1145, 1146, 1147, 1148, 1149, 1150, 1151, 1152, 1153, 1154]
        array is generated.

        Steps for seq_id=8 are the following:
            1) offset is set to 1000.
            2) offset is incremented by 160.
            3) offset is incremented by a random number (e.g., 2).
           4) [1162, 1163, 1164, 1165, 1166, 1167, 1168, 1169, 1170, 1171]
        array is generated.
        """
        offset = seq_id * self.array_size
        if self.is_random:
            offset = self.num_docs * self.array_size
            offset += 2 * seq_id * self.array_size
            offset += random.randint(1, self.array_size)

        return [offset + i for i in range(self.array_size)]

    def _build_achievements2(self, seq_id: int) -> List[int]:
        """Build an array of integers.

        achievements2 is very similar to achievements1. However, in case of
        achievements2 ranges overlap so that multiple documents case satisfy the
        same queries. Overlapping is achieving by integer division using
        ARRAY_CAP constant.
        """
        offset = seq_id // self.ARRAY_CAP * self.ARRAY_SIZE
        if self.is_random:
            offset = self.num_docs * self.ARRAY_SIZE
            offset += (2 * seq_id) // self.ARRAY_CAP * self.ARRAY_SIZE
            offset += random.randint(1, self.ARRAY_SIZE)

        return [offset + i for i in range(self.ARRAY_SIZE)]

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': self._build_name(alphabet),
            'email': self._build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'street': self._build_street(alphabet),
            'city': self._build_city(alphabet),
            'county': self._build_county(alphabet),
            'state': self._build_state(alphabet),
            'full_state': self._build_full_state(alphabet),
            'country': self._build_country(alphabet),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements1': self._build_achievements1(key.number + 1),
            'achievements2': self._build_achievements2(key.number + 1),
            'gmtime': self._build_gmtime(alphabet),
            'year': self._build_year(alphabet),
            'body': self._build_string(alphabet, size),
            'capped_small': self._build_capped(alphabet, key.number, 100),
            'topics': self._build_topics(key.number),
        }


class ProfileDocument(ReverseLookupDocument):

    OVERHEAD = 390

    def _build_capped(self, *args):
        capped = super()._build_capped(*args)
        return capped.replace('_', '')

    def _build_zip(self, seq_id: int) -> str:
        if self.is_random:
            zip_code = random.randint(70000, 90000)
        else:
            zip_code = 70000 + seq_id % 20000
        return str(zip_code)

    def _build_long_street(self, alphabet: str, seq_id: int, capped_small: str,
                           capped_large: str) -> str:
        if self.is_random:
            num = random.randint(0, 1000)
            idx = random.randint(0, NUM_STREET_SUFFIXES - 1)
        else:
            num = seq_id % 5000
            idx = alphabet.find('7') % NUM_STREET_SUFFIXES
        suffix = STREET_SUFFIX[idx]

        return '%d %s %s %s' % (num, capped_small, capped_large, suffix)

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        category = self._build_category(alphabet) + 1
        capped_large = self._build_capped(alphabet, key.number, 1000 * category)
        capped_small = self._build_capped(alphabet, key.number, 10)

        return {
            'first_name': self._build_name(alphabet),
            'last_name': self._build_street(alphabet),
            'email': self.build_email(alphabet),
            'balance': self._build_coins(alphabet),
            'date': {
                'gmtime': self._build_gmtime(alphabet),
                'year': self._build_year(alphabet),
            },
            'capped_large': capped_large,
            'address': {
                'street': self._build_long_street(alphabet,
                                                  key.number,
                                                  capped_small,
                                                  capped_large),
                'city': self._build_city(alphabet),
                'county': self._build_county(alphabet),
                'state': self._build_state(alphabet),
                'zip': self._build_zip(key.number),
                'realm': self._build_realm(alphabet),
            },
            'body': self._build_string(alphabet, size),
        }


class ImportExportDocument(ReverseLookupDocument):

    """Extend ReverseLookupDocument by adding 25 fields with random size."""

    OVERHEAD = 1022

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()
        return {
            'name': self._build_name(alphabet) * random.randint(0, 5),
            'email': self.build_email(alphabet) * random.randint(0, 5),
            'alt_email': self._build_alt_email(
                alphabet) * random.randint(0, 5),
            'street': self._build_street(alphabet) * random.randint(0, 9),
            'city': self._build_city(alphabet) * random.randint(0, 9),
            'county': self._build_county(alphabet) * random.randint(0, 5),
            'state': self._build_state(alphabet) * random.randint(0, 5),
            'full_state': self._build_full_state(
                alphabet) * random.randint(0, 5),
            'country': self._build_country(
                alphabet) * random.randint(0, 5),
            'realm': self._build_realm(
                alphabet) * random.randint(0, 9),
            'alt_street': self._build_street(
                alphabet) * random.randint(0, 9),
            'alt_city': self._build_city(
                alphabet) * random.randint(0, 9),
            'alt_county': self._build_county(
                alphabet) * random.randint(0, 5),
            'alt_state': self._build_state(
                alphabet) * random.randint(0, 5),
            'alt_full_state': self._build_full_state(
                alphabet) * random.randint(0, 5),
            'alt_country': self._build_country(
                alphabet) * random.randint(0, 5),
            'alt_realm': self._build_realm(
                alphabet) * random.randint(0, 9),
            'coins': self._build_coins(
                alphabet) * random.randint(0, 999),
            'category': self._build_category(
                alphabet) * random.randint(0, 5),
            'achievements': self._build_achievements(alphabet),
            'gmtime': self._build_gmtime(alphabet) * random.randint(0, 9),
            'year': self._build_year(alphabet) * random.randint(0, 5),
            'body': self._build_string(alphabet, size),
            'capped_small': self._build_capped(
                alphabet, key.number, 100) * random.randint(0, 5),
            'alt_capped_small': self._build_capped(
                alphabet, key.number, 100) * random.randint(0, 5),
        }


class ImportExportDocumentArray(ImportExportDocument):

    """Extend ImportExportDocument by adding array docs.

    The documents contain 25 top-level fields with variable-size arrays.
    """

    OVERHEAD = 0

    def _random_array(self, value: str, num: int):
        if value == '':
            return []
        l = len(value)
        if l < num:
            return [value] * 5
        scope = sorted(random.sample(range(l), num))
        result = [value[0 if i == 0 else scope[i - 1]:i + scope[i]] for i in range(num)]
        return result

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        # 25 Fields of random size. Have an array with at least 10 items in five fields.
        return {
            'name': self._random_array(self._build_name(
                alphabet) * random.randint(0, 9), 5),
            'email': self.build_email(
                alphabet) * random.randint(0, 5),
            'alt_email': self._build_alt_email(
                alphabet) * random.randint(0, 9),
            'street': self._random_array(self._build_street(
                alphabet) * random.randint(0, 9), 5),
            'city': self._random_array(self._build_city(
                alphabet) * random.randint(0, 9), 5),
            'county': self._random_array(self._build_county(
                alphabet) * random.randint(0, 9), 5),
            'state': self._random_array(self._build_state(
                alphabet) * random.randint(0, 9), 5),
            'full_state': self._random_array(self._build_full_state(
                alphabet) * random.randint(0, 9), 5),
            'country': self._random_array(self._build_country(
                alphabet) * random.randint(0, 9), 5),
            'realm': self._build_realm(alphabet) * random.randint(0, 9),
            'alt_street': self._random_array(self._build_street(
                alphabet) * random.randint(0, 9), 5),
            'alt_city': self._random_array(self._build_city(
                alphabet) * random.randint(0, 9), 5),
            'alt_county': self._build_county(
                alphabet) * random.randint(0, 9),
            'alt_state': self._build_state(
                alphabet) * random.randint(0, 9),
            'alt_full_state': self._build_full_state(
                alphabet) * random.randint(0, 9),
            'alt_country': self._build_country(
                alphabet) * random.randint(0, 9),
            'alt_realm': self._build_realm(
                alphabet) * random.randint(0, 9),
            'coins': self._build_coins(
                alphabet) * random.randint(0, 999),
            'category': self._build_category(
                alphabet) * random.randint(0, 9),
            'achievements': self._build_achievements(alphabet),
            'gmtime': self._build_gmtime(alphabet) * random.randint(0, 9),
            'year': self._build_year(alphabet) * random.randint(0, 5),
            'body': self._random_array(self._build_string(alphabet, size), 7),
            'capped_small': self._build_capped(
                alphabet, key.number, 100) * random.randint(0, 5),
            'alt_capped_small': self._build_capped(
                alphabet, key.number, 100) * random.randint(0, 5),
        }


class ImportExportDocumentNested(ImportExportDocument):

    """Extend ImportExportDocument by adding nested docs.

    The documents contain 25 top-level fields (5 nested sub-documents).
    """

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': {'n': {'a': {'m': {'e': self._build_name(
                alphabet) * random.randint(0, 3)}}}},
            'email': {'e': {'m': {'a': {'i': self.build_email(
                alphabet) * random.randint(0, 3)}}}},
            'alt_email': {'a': {'l': {'t': {'e': self._build_alt_email(
                alphabet) * random.randint(0, 3)}}}},
            'street': {'s': {'t': {'r': {'e': self._build_street(
                alphabet) * random.randint(0, 3)}}}},
            'city': {'c': {'i': {'t': {'y': self._build_city(
                alphabet) * random.randint(0, 3)}}}},
            'county': {'c': {'o': {'u': {'n': self._build_county(
                alphabet) * random.randint(0, 3)}}}},
            'state': {'s': {'t': {'a': {'t': self._build_state(
                alphabet) * random.randint(0, 3)}}}},
            'full_state': {'f': {'u': {'l': {'l': self._build_full_state(
                alphabet) * random.randint(0, 3)}}}},
            'country': {'c': {'o': {'u': {'n': self._build_country(
                alphabet) * random.randint(0, 3)}}}},
            'realm': {'r': {'e': {'a': {'l': self._build_realm(
                alphabet) * random.randint(0, 3)}}}},
            'alt_street': {'a': {'l': {'t': {'s': self._build_street(
                alphabet) * random.randint(0, 3)}}}},
            'alt_city': {'a': {'l': {'t': {'c': self._build_city(
                alphabet) * random.randint(0, 3)}}}},
            'alt_county': {'e': {'m': {'a': {'i': self._build_county(
                alphabet) * random.randint(0, 3)}}}},
            'alt_state': {'e': {'m': {'a': {'i': self._build_state(
                alphabet) * random.randint(0, 3)}}}},
            'alt_full_state': {'e': {'m': {'a': {'i': self._build_full_state(
                alphabet) * random.randint(0, 2)}}}},
            'alt_country': {'e': {'m': {'a': {'i': self._build_country(
                alphabet) * random.randint(0, 2)}}}},
            'alt_realm': {'e': {'m': {'a': {'i': self._build_realm(
                alphabet) * random.randint(0, 3)}}}},
            'coins': {'e': {'m': {'a': {'i': self._build_coins(
                alphabet) * random.randint(0, 99)}}}},
            'category': {'e': {'m': {'a': {'i': self._build_category(
                alphabet) * random.randint(0, 3)}}}},
            'achievements': self._build_achievements(alphabet),
            'gmtime': self._build_gmtime(alphabet) * random.randint(0, 2),
            'year': self._build_year(alphabet) * random.randint(0, 2),
            'body': self._build_string(alphabet, size),
            'capped_small': self._build_capped(
                alphabet, key.number, 10) * random.randint(0, 2),
            'alt_capped_small': self._build_capped(
                alphabet, key.number, 10) * random.randint(0, 2),
        }


class GSIMultiIndexDocument(Document):

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': self._build_alt_email(alphabet),
            'email': self._build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'city': self._build_alt_email(alphabet),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'body': self._build_string(alphabet, size),
        }


class PlasmaDocument(Document):

    @staticmethod
    def build_item(alphabet: str, size: int = 64, prefix: str = ""):
        length = size - len(prefix)
        num_slices = int(math.ceil(length / 64))  # 64 == len(alphabet)
        body = num_slices * alphabet
        num = random.randint(1, length)
        if prefix:
            return prefix + "-" + body[num:length] + body[0:num]
        return body[num:length] + body[0:num]


class SmallPlasmaDocument(PlasmaDocument):

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)

        return {
            'alt_email': self._build_alt_email(alphabet)
        }


class SequentialPlasmaDocument(PlasmaDocument):

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        number = key[-12:]  # FIXME

        return {
            'city': self.build_item(alphabet=alphabet, size=17, prefix=number)
        }


class LargeItemPlasmaDocument(PlasmaDocument):

    def __init__(self, avg_size: int, item_size: int):
        super().__init__(avg_size)
        self.item_size = item_size

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()

        return {
            'name': self._build_name(alphabet),
            'email': self._build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'city': self.build_item(alphabet=alphabet, size=self.item_size),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'body': self._build_string(alphabet, size),
        }


class VaryingItemSizePlasmaDocument(PlasmaDocument):

    def __init__(self, avg_size: int, size_variation_min: int,
                 size_variation_max: int):
        super().__init__(avg_size)
        self.size_variation_min = size_variation_min
        self.size_variation_max = size_variation_max

    def next(self, key: Key) -> dict:
        alphabet = self._build_alphabet(key.string)
        size = self._size()
        length = random.randint(self.size_variation_min, self.size_variation_max)

        return {
            'name': self._build_name(alphabet),
            'email': self._build_email(alphabet),
            'alt_email': self._build_alt_email(alphabet),
            'city': self.build_item(alphabet=alphabet, size=length),
            'realm': self._build_realm(alphabet),
            'coins': self._build_coins(alphabet),
            'category': self._build_category(alphabet),
            'achievements': self._build_achievements(alphabet),
            'body': self._build_string(alphabet, size),
        }
