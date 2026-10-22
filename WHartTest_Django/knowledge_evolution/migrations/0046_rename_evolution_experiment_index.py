from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0045_evolutionexperiment_evolutionvariant"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="evolutionexperiment",
            old_name="knowledge_ev_project_4420bd_idx",
            new_name="ke_evoexp_project_state_idx",
        ),
    ]
