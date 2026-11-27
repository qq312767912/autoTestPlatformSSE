from pathlib import Path
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from PIL import Image, UnidentifiedImageError
from uuid import uuid4

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from projects.models import Project
from .models import RequirementComparison
from .document_comparison import ComparisonError, compare_documents, export_selection, SCHEMA_VERSION
from .pdf_comparison import pdf_as_docx


def comparison_inputs(old, new, extension):
    if extension == '.pdf':
        return pdf_as_docx(old), pdf_as_docx(new)
    return old, new


def compare_sources(old, new, extension):
    result = compare_documents(*comparison_inputs(old, new, extension))
    if extension == '.pdf':
        result['warnings'] = ['仅比较 PDF 可提取的文本。图片、扫描页和排版不参与对比；请核对复杂版式的阅读顺序。']
    return result


class RequirementComparisonViewSet(viewsets.GenericViewSet):
    """Two uploaded files -> reviewable diff -> explicitly confirmed extraction."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = RequirementComparison.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        # Review sessions are private to their creator; membership is checked again.
        return queryset.filter(creator=self.request.user, project__members__user=self.request.user).distinct()

    def create(self, request):
        project_id = request.data.get('project')
        if not project_id:
            raise ValidationError({'project': '请选择项目。'})
        projects = Project.objects.all() if request.user.is_superuser else Project.objects.filter(members__user=request.user)
        try:
            project = get_object_or_404(projects.distinct(), pk=project_id)
        except (ValueError, DjangoValidationError):
            raise ValidationError({'project': '项目编号无效。'})
        old, new = request.FILES.get('old_file'), request.FILES.get('new_file')
        if not old or not new:
            raise ValidationError('请同时上传旧版和新版文档。')
        extension = Path(old.name).suffix.lower()
        if extension not in ('.docx', '.pdf') or Path(new.name).suffix.lower() != extension:
            raise ValidationError('请上传两份同格式的 DOCX 或 PDF 文件。')
        try:
            result = compare_sources(old.read(), new.read(), extension)
        except ComparisonError as exc:
            raise ValidationError(str(exc))
        old.seek(0)
        new.seek(0)
        record = RequirementComparison(project=project, creator=request.user,
                                       old_name=old.name[:255], new_name=new.name[:255], result=result)
        try:
            record.old_file.save(f'{uuid4().hex}{extension}', old, save=False)
            record.new_file.save(f'{uuid4().hex}{extension}', new, save=False)
            record.save()
        except Exception:
            for field in (record.old_file, record.new_file):
                if field.name:
                    field.delete(save=False)
            raise
        return Response(self.payload(record), status=status.HTTP_201_CREATED)

    def payload(self, record):
        return {'id': str(record.id), 'old_name': record.old_name, 'new_name': record.new_name,
                'result': record.result, 'confirmed_hunks': record.confirmed_hunks,
                'iteration_title': record.iteration_title, 'confirmed_at': record.confirmed_at}

    def retrieve(self, request, pk=None):
        record = self.get_object()
        if record.result.get('schema_version') != SCHEMA_VERSION:
            try:
                with record.old_file.open('rb') as old, record.new_file.open('rb') as new:
                    record.result = compare_sources(old.read(), new.read(), Path(record.new_name).suffix.lower())
            except ComparisonError as exc:
                raise ValidationError(str(exc))
            record.confirmed_hunks = []
            record.confirmed_at = None
            record.save(update_fields=['result', 'confirmed_hunks', 'confirmed_at'])
        return Response(self.payload(record))

    @action(detail=True, methods=['get'], url_path='image')
    def image(self, request, pk=None):
        record = self.get_object()
        side, rid = request.query_params.get('side'), request.query_params.get('rid')
        if side not in ('old', 'new') or not rid:
            raise ValidationError('图片参数无效。')
        asset = record.result.get('assets', {}).get(side, {}).get(rid)
        if not asset or not asset.get('content_type', '').startswith('image/'):
            raise ValidationError('图片不属于本次对比。')
        source = record.old_file if side == 'old' else record.new_file
        try:
            with source.open('rb') as stream, ZipFile(stream) as package:
                content = package.read(asset['part_name'])
            with Image.open(BytesIO(content)) as image:
                image.thumbnail((900, 700))
                output = BytesIO()
                image.convert('RGBA').save(output, format='PNG')
        except (UnidentifiedImageError, OSError, KeyError, BadZipFile, Image.DecompressionBombError):
            raise ValidationError('此图片格式暂无法预览，原图仍保留在导出文档中。')
        response = HttpResponse(output.getvalue(), content_type='image/png')
        response['Cache-Control'] = 'private, max-age=300'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @action(detail=True, methods=['post'], url_path='confirm-extract')
    def confirm_extract(self, request, pk=None):
        record = self.get_object()
        title = request.data.get('title')
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ValidationError('请输入不超过200字的迭代名称。')
        if any(ord(char) < 32 and char not in '\t\n\r' for char in title):
            raise ValidationError('迭代名称含无效字符。')
        selected = request.data.get('hunk_ids')
        try:
            with record.new_file.open('rb') as source, record.old_file.open('rb') as baseline:
                old_data, new_data = comparison_inputs(baseline.read(), source.read(), Path(record.new_name).suffix.lower())
                bundle = export_selection(new_data, record.result, selected, title.strip(), old_data, record.new_name, docx_only=True)
        except ComparisonError as exc:
            raise ValidationError(str(exc))
        record.confirmed_hunks = selected
        record.iteration_title = title.strip()
        record.confirmed_at = timezone.now()
        record.save(update_fields=['confirmed_hunks', 'iteration_title', 'confirmed_at'])
        response = HttpResponse(bundle, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        response['Content-Disposition'] = 'attachment; filename="iteration-diff.docx"'
        response['Cache-Control'] = 'no-store'
        return response
