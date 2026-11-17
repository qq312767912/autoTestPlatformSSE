from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from projects.models import Project
from .models import UiModule, UiPage, UiPageSteps, UiPageStepsDetailed, UiTestCase, UiCaseStepsDetailed


class AssetBatchDeleteTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='asset-delete-check',password='test')
        self.project=Project.objects.create(name='asset deletion test',creator=self.user)
        self.module=UiModule.objects.create(project=self.project,name='module',creator=self.user)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def page(self,name='page'):
        return UiPage.objects.create(project=self.project,module=self.module,name=name,creator=self.user)

    def post(self,kind,ids,project=None):
        return self.client.post(f'/api/ui-automation/{kind}/batch-delete/',{'project':project or self.project.pk,'ids':ids},format='json')

    def result(self,response):
        data=response.data
        return data.get('data',data)

    def test_pages_delete_only_unreferenced_selected_assets(self):
        free=self.page('free');blocked=self.page('blocked');untouched=self.page('unselected')
        UiPageSteps.objects.create(project=self.project,module=self.module,page=blocked,name='ref',creator=self.user)
        ids=[free.pk,blocked.pk]
        response=self.post('pages',ids)
        self.assertEqual(response.status_code,200)
        data=self.result(response)
        self.assertEqual(data['deleted_count'],1)
        self.assertEqual([x['id'] for x in data['blocked']],[blocked.pk])
        self.assertEqual(UiPage.objects.filter(pk__in=[blocked.pk,untouched.pk]).count(),2)
        self.assertFalse(UiPage.objects.filter(pk=free.pk).exists())

    def test_steps_preserve_testcase_references(self):
        page=self.page()
        free=UiPageSteps.objects.create(project=self.project,module=self.module,page=page,name='free',creator=self.user)
        blocked=UiPageSteps.objects.create(project=self.project,module=self.module,page=page,name='blocked',creator=self.user)
        detail=UiPageStepsDetailed.objects.create(page_step=free,step_type=0,ope_key='wait',ope_value={})
        case=UiTestCase.objects.create(project=self.project,module=self.module,name='case',creator=self.user)
        UiCaseStepsDetailed.objects.create(test_case=case,page_step=blocked)
        response=self.post('page-steps',[free.pk,blocked.pk])
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.result(response)['deleted_count'],1)
        self.assertFalse(UiPageStepsDetailed.objects.filter(pk=detail.pk).exists())
        self.assertTrue(UiPageSteps.objects.filter(pk=blocked.pk).exists())
        self.assertTrue(case.case_steps.filter(page_step=blocked).exists())

    def test_cross_project_selection_rejects_entire_request(self):
        free=self.page();other=Project.objects.create(name='other',creator=self.user)
        module=UiModule.objects.create(project=other,name='other',creator=self.user)
        foreign=UiPage.objects.create(project=other,module=module,name='foreign',creator=self.user)
        self.assertEqual(self.post('pages',[free.pk,foreign.pk]).status_code,400)
        self.assertTrue(UiPage.objects.filter(pk=free.pk).exists())
        self.assertTrue(UiPage.objects.filter(pk=foreign.pk).exists())

    def test_missing_delete_permission_and_invalid_ids(self):
        free=self.page()
        self.assertEqual(self.post('pages',[True]).status_code,400)
        self.assertEqual(self.post('pages',[]).status_code,400)
        user=get_user_model().objects.create_user(username='no-delete')
        self.client.force_authenticate(user)
        self.assertEqual(self.post('pages',[free.pk]).status_code,403)
        self.assertTrue(UiPage.objects.filter(pk=free.pk).exists())
