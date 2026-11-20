"""只读预检；被动查询404表示队列不存在，不能吞掉连接/权限错误。"""
from amqp.exceptions import ChannelError


def pending_count(connection, name):
    # AMQP 的404可能关闭channel；每个队列单独使用channel。
    channel = connection.channel()
    try:
        return channel.queue_declare(queue=name, passive=True).message_count
    except ChannelError as exc:
        if exc.reply_code != 404:
            raise
        return 0
    finally:
        channel.close()


def main():
    from wharttest_django.celery import app
    inspector = app.control.inspect(timeout=5)
    for method in (inspector.active, inspector.reserved, inspector.scheduled):
        result = method()
        assert result and not any(result.values()), 'worker繁忙、延时/预取任务未排空或未响应，请等待任务结束'
    with app.connection_for_read() as connection:
        for name in ['celery', 'task_center', 'code_analysis', 'testcase_review', 'knowledge', 'ui_execution']:
            count = pending_count(connection, name)
            assert count == 0, '队列尚有任务：%s (%s)' % (name, count)
    print('运行中、预取、延时任务及队列积压检查通过')


if __name__ == '__main__':
    main()
