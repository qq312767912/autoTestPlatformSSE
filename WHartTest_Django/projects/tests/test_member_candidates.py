from django.test import TestCase
from django.contrib.auth.models import User, Permission
from rest_framework.test import APIClient
from projects.models import Project, ProjectMember


class ProjectMemberCandidatesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner')
        self.target = User.objects.create_user('candidate')
        self.outsider = User.objects.create_user('outsider')
        User.objects.create_user('inactive', is_active=False)
        self.project = Project.objects.create(name='owned', creator=self.owner)
        ProjectMember.objects.create(project=self.project, user=self.owner, role='owner')
        self.client = APIClient()

    def test_owner_without_global_user_permission_can_find_and_add(self):
        self.client.force_authenticate(self.owner)
        url=f'/api/projects/{self.project.pk}/'
        r=self.client.get(url+'member_candidates/', {'search':'candidate'})
        self.assertEqual(r.status_code,200,r.data)
        rows=r.data['results'] if isinstance(r.data,dict) else r.data
        self.assertEqual(list(rows),[{'id':self.target.pk,'username':'candidate','email':''}])
        r=self.client.post(url+'add_member/', {'user_id':self.target.pk,'role':'member'})
        self.assertEqual(r.status_code,201,r.data)
        r=self.client.get(url+'member_candidates/', {'search':'candidate'})
        rows=r.data['results'] if isinstance(r.data,dict) else r.data
        self.assertEqual(list(rows),[])

    def test_outsider_cannot_enumerate_candidates(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f'/api/projects/{self.project.pk}/member_candidates/').status_code,403)

    def test_create_permission_and_owner_membership(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.post('/api/projects/',{'name':'denied'}).status_code,403)
        self.outsider.user_permissions.add(Permission.objects.get(codename='add_project'))
        self.client.force_authenticate(User.objects.get(pk=self.outsider.pk))
        r=self.client.post('/api/projects/',{'name':'allowed','credentials':[]},format='json')
        self.assertEqual(r.status_code,201,r.data)
        self.assertTrue(ProjectMember.objects.filter(project_id=r.data['id'],user=self.outsider,role='owner').exists())
