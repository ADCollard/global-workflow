import importlib.util
import logging
import shutil
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
JEDI_PATH = REPO_ROOT / 'ush/python/pygfs/jedi/jedi.py'


def load_jedi_module(monkeypatch):
    wxflow = types.ModuleType('wxflow')

    class AttrDict(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError as err:
                raise AttributeError(name) from err

        def __setattr__(self, name, value):
            self[name] = value

        def deepcopy(self):
            return AttrDict(self)

    class WorkflowKeyError(KeyError):
        pass

    def logit(_logger):
        def decorator(func):
            return func
        return decorator

    wxflow.AttrDict = AttrDict
    wxflow.FileHandler = object
    wxflow.Task = object
    wxflow.Executable = object
    wxflow.WorkflowException = Exception
    wxflow.WorkflowKeyError = WorkflowKeyError
    wxflow.WorkflowTypeError = TypeError
    wxflow.chdir = lambda *args, **kwargs: None
    wxflow.parse_j2yaml = lambda *args, **kwargs: {}
    wxflow.save_as_yaml = lambda *args, **kwargs: None
    wxflow.logit = logit

    jcb = types.ModuleType('jcb')
    jcb.render = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, 'wxflow', wxflow)
    monkeypatch.setitem(sys.modules, 'jcb', jcb)

    spec = importlib.util.spec_from_file_location('test_jedi_module', JEDI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingFileHandler:
    calls = []

    def __init__(self, config):
        self.config = config
        RecordingFileHandler.calls.append(config)

    def sync(self):
        for directory in self.config.get('mkdir', []):
            Path(directory).mkdir(parents=True, exist_ok=True)
        for source, destination in self.config.get('copy_opt', []):
            Path(destination).mkdir(parents=True, exist_ok=True)
            shutil.copy(source, destination)


def test_stage_obsbiasin_stages_shared_varbc_tarball_once(tmp_path, monkeypatch):
    module = load_jedi_module(monkeypatch)
    RecordingFileHandler.calls = []
    monkeypatch.setattr(module, 'FileHandler', RecordingFileHandler)

    extracted = []
    monkeypatch.setattr(module.Jedi, 'extract_tar', staticmethod(lambda tar_file: extracted.append(tar_file)))

    comin = tmp_path / 'comin'
    obsbiasin = tmp_path / 'obsbiasin'
    obsbiasout = tmp_path / 'obsbiasout'
    comin.mkdir()
    (comin / 'gdas.varbc_params.tar').write_text('stub tarball', encoding='ascii')

    jedi = object.__new__(module.Jedi)
    jedi.component = 'gdas'
    jedi.jcb_config = {
        'observations': ['obs_a', 'obs_b', 'obs_a'],
        'gdas_obsbiasin_path': str(obsbiasin),
        'gdas_obsbiasout_path': str(obsbiasout),
        'gdas_obsbiasin_prefix': 'gdas.',
    }

    jedi.stage_obsbiasin(str(comin))

    assert RecordingFileHandler.calls == [{
        'mkdir': [str(obsbiasin), str(obsbiasout)],
        'copy_opt': [[str(comin / 'gdas.varbc_params.tar'), str(obsbiasin)]],
    }]
    assert extracted == [str(obsbiasin / 'gdas.varbc_params.tar')]


def test_stage_obsbiasin_warns_and_skips_missing_shared_tarball(tmp_path, monkeypatch, caplog):
    module = load_jedi_module(monkeypatch)
    RecordingFileHandler.calls = []
    monkeypatch.setattr(module, 'FileHandler', RecordingFileHandler)

    extracted = []
    monkeypatch.setattr(module.Jedi, 'extract_tar', staticmethod(lambda tar_file: extracted.append(tar_file)))

    comin = tmp_path / 'comin'
    obsbiasin = tmp_path / 'obsbiasin'
    obsbiasout = tmp_path / 'obsbiasout'
    comin.mkdir()

    jedi = object.__new__(module.Jedi)
    jedi.component = 'gdas'
    jedi.jcb_config = {
        'observations': ['obs_a', 'obs_a'],
        'gdas_obsbiasin_path': str(obsbiasin),
        'gdas_obsbiasout_path': str(obsbiasout),
        'gdas_obsbiasin_prefix': 'gdas.',
    }

    with caplog.at_level(logging.WARNING):
        jedi.stage_obsbiasin(str(comin))

    assert RecordingFileHandler.calls == [{
        'mkdir': [str(obsbiasin), str(obsbiasout)],
        'copy_opt': [],
    }]
    assert extracted == []
    assert f"Bias correction file {comin / 'gdas.varbc_params.tar'} does not exist and will be skipped" in caplog.text
