"""Run comparison regression tests against a disposable SQLite database, never production."""
import sys
import tempfile
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'WHartTest_Django'))
from django.conf import settings

with tempfile.TemporaryDirectory(prefix='whart-comparison-tests-') as media:
    settings.configure(
        SECRET_KEY='local-comparison-tests-only',
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'projects', 'requirements', 'rest_framework'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
        MEDIA_ROOT=media, USE_TZ=True, ROOT_URLCONF='comparison_test_urls',
        REST_FRAMEWORK={'DEFAULT_AUTHENTICATION_CLASSES': [], 'UNAUTHENTICATED_USER': None},
        MIDDLEWARE=[],
    )
    import django
    django.setup()
    from rest_framework.routers import DefaultRouter
    from requirements.comparison_views import RequirementComparisonViewSet
    router = DefaultRouter()
    router.register('comparisons', RequirementComparisonViewSet)
    url_module = types.ModuleType('comparison_test_urls')
    url_module.urlpatterns = router.urls
    sys.modules['comparison_test_urls'] = url_module
    from django.core.management import call_command
    call_command('makemigrations', 'requirements', check=True, dry_run=True, verbosity=1)
    from django.test.utils import get_runner
    runner = get_runner(settings)(verbosity=2)
    failures = runner.run_tests(['requirements.test_document_comparison', 'requirements.test_comparison_api'])
    raise SystemExit(bool(failures))
