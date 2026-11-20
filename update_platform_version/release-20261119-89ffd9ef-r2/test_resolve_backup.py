import importlib.util,pathlib,tempfile,json,unittest
s=importlib.util.spec_from_file_location('resolver',pathlib.Path(__file__).parent/'deploy_env/resolve_backup.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class ResolverTests(unittest.TestCase):
 def test_missing_pointer_ignores_incomplete_and_wrong_release(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);here=root/'deploy_env';here.mkdir();(here/'VERSION').write_text('test')
   def backup(name,version,complete):
    p=root/'backups'/name;p.mkdir(parents=True);(p/'project').write_text('offline-images');(p/'compose.rollback.json').write_text(json.dumps({'services':{'backend':{'image':'old'}}}));(p/'compose.runtime.json').write_text(json.dumps({'services':{'backend':{'image':'wharttest-250-backend:update-'+version+'-arm64'}}}))
    if complete:
     (p/'BACKUP_COMPLETE').touch()
     for name in ['postgres.sql.gz','app-data.tar.gz','stopped-containers']:(p/name).write_text('test')
    return p
   good=backup('20261120-110000','test',True);backup('20261120-120000','test',False);backup('20261120-130000','other',True)
   self.assertEqual(m.resolve(here),good.resolve());self.assertEqual(m.resolve(here),good.resolve())
 def test_no_complete_backup_fails(self):
  with tempfile.TemporaryDirectory() as d:
   here=pathlib.Path(d)/'deploy_env';here.mkdir();(here/'VERSION').write_text('test')
   with self.assertRaises(RuntimeError):m.resolve(here)
if __name__=='__main__':unittest.main()
