import asyncio
import asynctest
from unittest.mock import ANY

from bson import Timestamp
from motor.motor_asyncio import AsyncIOMotorClient

from mongo_observer.models import Operations
from mongo_observer.observer import Observer, ShouldStopObservation
from mongo_observer.operation_handlers import OperationHandler
from tests import conf


class ObserverObserveChangesTests(asynctest.TestCase):
    async def setUp(self):
        class FooHandler(OperationHandler):
            on_insert = asynctest.CoroutineMock()
            on_delete = asynctest.CoroutineMock()
            on_update = asynctest.CoroutineMock()

        self.handler = FooHandler()

        self.client = AsyncIOMotorClient(**conf.MONGO_CONN_PARAMS)
        self.oplog_db = self.client[conf.OPLOG_DATABASE]
        self.oplog_collection = self.oplog_db[conf.OPLOG_COLLECTION]
        self.db = self.client[conf.TEST_DATABASE]
        self.collection = self.db[conf.TEST_COLLECTION]

        self.mock_doc = {'db': 'mongo', 'is': 'awesome', 'author': 'diogommartins'}
        await self.collection.insert_one(self.mock_doc)
        await asyncio.sleep(1)  # wait for `ts` to change

        self.stating_ts = Timestamp(self.mock_doc['_id'].generation_time, 0)

        self.observer = await Observer.init_async(
            oplog=self.oplog_collection,
            operation_handler=self.handler,
            namespace_filter=f'{self.db.name}.{self.collection.name}',
            starting_timestamp=self.stating_ts,
            on_nothing_to_fetch_on_cursor=self.stop_infinite_iteration
        )

    async def tearDown(self):
        await self.collection.delete_many({})

    async def stop_infinite_iteration(self):
        raise ShouldStopObservation

    async def observe_and_stop(self):
        with self.assertRaises(ShouldStopObservation):
            await self.observer.observe_changes()

    async def test_it_observes_insert_operations(self):
        doc = {'name': 'Xablau', 'age': 2}
        await self.collection.insert_one(doc)

        await self.observe_and_stop()

        self.handler.on_delete.assert_not_called()
        self.handler.on_update.assert_not_called()
        self.handler.on_insert.assert_called_once_with(
            {
                'ts': ANY,
                't': ANY,
                'h': ANY,
                'v': ANY,
                'op': 'i',
                'o': doc,
                'ns': self.observer.namespace_filter
            }
        )

    async def test_it_observes_update_operations(self):
        update_op = {"$set": {"author": "Xablau"}}
        res = await self.collection.update_one({'_id': self.mock_doc['_id']}, update_op)

        await self.observe_and_stop()

        self.handler.on_delete.assert_not_called()
        self.handler.on_insert.assert_not_called()
        self.handler.on_update.assert_called_once_with(
            {
                'ts': ANY,
                't': ANY,
                'h': ANY,
                'v': ANY,
                'op': 'u',
                'o': update_op,
                'o2': {'_id': self.mock_doc['_id']},
                'ns': self.observer.namespace_filter
            }
        )

    async def test_it_observes_delete_operations(self):
        res = await self.collection.delete_one({"_id": self.mock_doc['_id']})

        await self.observe_and_stop()

        self.handler.on_update.assert_not_called()
        self.handler.on_insert.assert_not_called()
        self.handler.on_delete.assert_called_once_with(
            {
                'ts': ANY,
                't': ANY,
                'h': ANY,
                'v': ANY,
                'op': 'd',
                'o': {'_id': self.mock_doc['_id']},
                'ns': self.observer.namespace_filter
            }
        )

    async def test_it_calls_handlers_for_each_affected_document(self):
        docs = [{'i': i, 'dog': 'Xablau'} for i in range(5)]

        insert_result = await self.collection.insert_many(docs)
        delete_result = await self.collection.delete_many({'dog': 'Xablau'})
        self.assertEqual(len(insert_result.inserted_ids), delete_result.deleted_count)

        await self.observe_and_stop()

        self.handler.on_update.assert_not_called()

        for doc in docs:
            self.handler.on_insert.assert_any_call(
                {
                    'ts': ANY,
                    't': ANY,
                    'h': ANY,
                    'v': ANY,
                    'op': Operations.INSERT,
                    'o': doc,
                    'ns': self.observer.namespace_filter
                }
            )
            self.handler.on_delete.assert_any_call(
                {
                    'ts': ANY,
                    't': ANY,
                    'h': ANY,
                    'v': ANY,
                    'op': Operations.DELETE,
                    'o': {'_id': doc['_id']},
                    'ns': self.observer.namespace_filter
                }
            )
