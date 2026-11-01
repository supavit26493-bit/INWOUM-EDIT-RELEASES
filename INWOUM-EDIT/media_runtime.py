"""Diagnose PyAV and repair its wheel without touching jobs, models or API keys."""
import argparse,importlib,importlib.util,json,subprocess,sys,sysconfig,tempfile,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PIN='av==19.0.1'

def package_roots():
    paths=sysconfig.get_paths()
    return [Path(paths[k]).resolve() for k in ('purelib','platlib')]

def module_problem(module,roots):
    location=getattr(module,'__file__',None)
    if not location:return 'PyAV has no package file; installation may be incomplete.'
    if not any(Path(location).resolve().is_relative_to(Path(p).resolve()) for p in roots):
        return 'A local file/folder named av is shadowing PyAV. Rename that file/folder; do not rename your media.'
    if not callable(getattr(module,'open',None)) or not callable(getattr(module,'AudioResampler',None)):
        return 'PyAV is incomplete: av.open or AudioResampler is missing.'
    return None

def inspect_media():
    # Match the application's import paths, including accidental local av.py/av folders.
    sys.path[:0]=[str(ROOT/'core'),str(ROOT)]
    roots=package_roots()
    result=dict(python=sys.executable,package='av',expected=PIN,status='broken')
    try:
        spec=importlib.util.find_spec('av')
        if spec is None:return dict(result,status='missing',error='PyAV is not installed.')
        result['module_file']=spec.origin
        if spec.origin and not any(Path(spec.origin).resolve().is_relative_to(p) for p in roots):
            return dict(result,status='shadowed',error='A local av.py/av folder shadows PyAV. Rename the local file/folder first.')
        module=importlib.import_module('av');problem=module_problem(module,roots)
        if problem:return dict(result,error=problem)
        with tempfile.TemporaryDirectory(prefix='inwoum-av-') as tmp:
            sample=Path(tmp)/'sample.wav'
            with wave.open(str(sample),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*1600)
            with module.open(str(sample)) as container:
                samples=sum(frame.samples for frame in container.decode(audio=0))
            if samples!=1600:raise RuntimeError('PyAV could not decode the test WAV completely.')
        return dict(result,status='ok',version=getattr(module,'__version__','unknown'),decoded_samples=1600)
    except Exception as error:return dict(result,error=f'{type(error).__name__}: {error}')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');parser.add_argument('--repair',action='store_true');args=parser.parse_args()
    result=inspect_media();print(json.dumps(result,ensure_ascii=True),flush=True)
    if result['status']=='ok':return 0
    if not args.repair or result['status']=='shadowed':return 1
    print('Reinstalling the pinned PyAV wheel. Internet is required. Jobs and keys are unchanged.',flush=True)
    process=subprocess.run([sys.executable,'-I','-m','pip','install','--only-binary=:all:','--no-cache-dir','--force-reinstall','--no-deps',PIN])
    if process.returncode:return process.returncode
    # A fresh process avoids reusing an already-imported broken module/DLL.
    return subprocess.run([sys.executable,'-I',str(Path(__file__).resolve()),'--check']).returncode

if __name__=='__main__':raise SystemExit(main())
