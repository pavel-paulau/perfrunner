BROKER_URL = 'amqp://couchbase:couchbase@172.23.97.73:5672/broker'
BROKER_POOL_LIMIT = None
CELERY_RESULT_BACKEND = 'amqp'
CELERY_RESULT_EXCHANGE = 'perf_results'
CELERY_RESULT_PERSISTENT = False
CELERYD_HIJACK_ROOT_LOGGER = False
