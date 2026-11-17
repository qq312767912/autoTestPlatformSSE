from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from projects.models import Project
from .models import UiModule, UiPage, UiPageSteps


class ModuleDeletionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='module-delete-check',password='test')
        self.project=Project.objects.create(name='module deletion test',creator=self.user)
        self.module=UiModule.objects.create(project=self.project,name='parent',creator=self.user)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def delete(self, module):
        return self.client.delete(f'/api/ui-automation/modules/{module.pk}/')

    def test_empty_module_can_be_deleted(self):
        pk=self.module.pk
        self.assertEqual(self.delete(self.module).status_code,200)  # 平台统一响应层将删除成功规范为 200
        self.assertFalse(UiModule.objects.filter(pk=pk).exists())

    def test_page_reference_returns_json_error_without_deleting_assets(self):
        page=UiPage.objects.create(project=self.project,module=self.module,name='retained',creator=self.user)
        response=self.delete(self.module)
        self.assertEqual(response.status_code,400)
        self.assertIn('页面',response.data['error'])
        self.assertTrue(UiModule.objects.filter(pk=self.module.pk).exists())
        self.assertTrue(UiPage.objects.filter(pk=page.pk).exists())

    def test_descendant_page_step_prevents_partial_tree_deletion(self):
        child=UiModule.objects.create(project=self.project,parent=self.module,name='child',creator=self.user)
        other=UiModule.objects.create(project=self.project,name='other',creator=self.user)
        page=UiPage.objects.create(project=self.project,module=other,name='shared page',creator=self.user)
        step=UiPageSteps.objects.create(project=self.project,module=child,page=page,name='retained step',creator=self.user)
        response=self.delete(self.module)
        self.assertEqual(response.status_code,400)
        self.assertIn('页面步骤',response.data['error'])
        self.assertEqual(UiModule.objects.filter(pk__in=[self.module.pk,child.pk]).count(),2)
        self.assertTrue(UiPageSteps.objects.filter(pk=step.pk).exists())
