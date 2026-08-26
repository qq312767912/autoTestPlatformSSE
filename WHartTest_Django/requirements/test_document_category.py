from django.contrib.auth.models import User
from django.test import TestCase

from projects.models import Project

from .filters import RequirementDocumentFilter
from .models import RequirementDocument
from .serializers import RequirementDocumentSerializer, RequirementDocumentUploadSerializer


class RequirementDocumentCategoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="document-category-user")
        self.project = Project.objects.create(name="Document category project", creator=self.user)

    def test_existing_workflow_defaults_to_business_requirement(self):
        document = RequirementDocument.objects.create(
            project=self.project,
            uploader=self.user,
            title="Legacy requirement",
            document_type="txt",
            content="requirement content",
        )

        self.assertEqual(document.document_category, "business_requirement")
        self.assertEqual(
            RequirementDocumentSerializer(document).data["document_category"],
            "business_requirement",
        )

    def test_upload_serializer_accepts_all_document_categories(self):
        for category, _label in RequirementDocument.DOCUMENT_CATEGORIES:
            serializer = RequirementDocumentUploadSerializer(data={
                "title": f"Document {category}",
                "document_type": "txt",
                "document_category": category,
                "content": "document content",
                "project": self.project.pk,
            })
            self.assertTrue(serializer.is_valid(), serializer.errors)
            document = serializer.save(uploader=self.user)
            self.assertEqual(document.document_category, category)

    def test_document_list_can_filter_by_category(self):
        for category, _label in RequirementDocument.DOCUMENT_CATEGORIES:
            RequirementDocument.objects.create(
                project=self.project,
                uploader=self.user,
                title=f"Document {category}",
                document_type="txt",
                document_category=category,
                content="document content",
            )

        queryset = RequirementDocumentFilter(
            {"document_category": "technical_design"},
            queryset=RequirementDocument.objects.all(),
        ).qs

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.get().document_category, "technical_design")
