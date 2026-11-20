import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock
from amqp.exceptions import ChannelError

spec = importlib.util.spec_from_file_location('queue_check', Path(__file__).parent/'deploy_env/check_queues.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class QueueCheckTests(unittest.TestCase):
    def test_missing_queue_is_empty(self):
        c=Mock(); c.channel.return_value.queue_declare.side_effect=ChannelError('missing', reply_code=404)
        self.assertEqual(m.pending_count(c,'missing'),0)
        c.channel.return_value.close.assert_called_once()
    def test_counts_are_preserved(self):
        for count in (0,3):
            c=Mock();c.channel.return_value.queue_declare.return_value.message_count=count
            self.assertEqual(m.pending_count(c,'queue'),count)
    def test_permission_and_transport_errors_are_not_ignored(self):
        for error in (ChannelError('denied',reply_code=403),ConnectionError('disconnected')):
            c=Mock();c.channel.return_value.queue_declare.side_effect=error
            with self.assertRaises(type(error)):m.pending_count(c,'queue')
            c.channel.return_value.close.assert_called_once()

if __name__=='__main__': unittest.main()
