"""Optional Windows desktop window. The editing backend remains shared with web mode."""
import argparse,os,sys,webbrowser
from pathlib import Path
import runtime_session as session
ROOT=Path(__file__).resolve().parent

APP_ID='INWOUM.EDIT.Desktop'

def set_app_id():
 if os.name!='nt':return
 import ctypes
 call=ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
 call.argtypes=[ctypes.c_wchar_p];call.restype=ctypes.c_long
 if call(APP_ID)!=0:raise OSError('Cannot set INWOUM EDIT taskbar identity')

def run(port=8776):
 if os.name!='nt':raise RuntimeError('รุ่นเดสก์ท็อปนี้รองรับ Windows กรุณาใช้ START-WEB.cmd')
 guard=session.Lock(ROOT,'desktop')
 if not guard.acquire():
  message('แอป INWOUM EDIT เปิดอยู่แล้ว กรุณาเลือกหน้าต่างเดิมจากแถบงาน');return
 try:
  set_app_id()
  import webview
  url=session.ensure(ROOT,port)
  profile=session.home(ROOT)/'desktop-profile';profile.mkdir(parents=True,exist_ok=True)
  webview.settings['ALLOW_DOWNLOADS']=True
  webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
  webview.settings['ALLOW_FILE_URLS']=False
  window=webview.create_window('INWOUM EDIT',url,width=1380,height=900,min_size=(820,620),background_color='#252c34',text_select=True,zoomable=True)
  window.events.closed += guard.release
  # No Python/JavaScript bridge is exposed. The UI uses the same authenticated local HTTP API.
  webview.start(gui='edgechromium',private_mode=False,storage_path=str(profile),debug=False,icon=str(ROOT/'assets/inwoumedit.ico'))
 finally:guard.release()

def message(text):
 import ctypes
 ctypes.windll.user32.MessageBoxW(None,text,'INWOUM EDIT',0x40)

def main():
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8776);args=p.parse_args()
 try:run(args.port)
 except Exception as error:
  folder=session.home(ROOT);folder.mkdir(parents=True,exist_ok=True)
  with (folder/'desktop.log').open('a',encoding='utf8') as out:out.write(str(error)+'\n')
  message('เปิดหน้าต่างแอปไม่สำเร็จ\n\n'+str(error)+'\n\nยังเปิดแบบเว็บด้วย START-WEB.cmd ได้\nหากขาด WebView2 ให้ติดตั้ง Microsoft Edge WebView2 Runtime จาก Microsoft')
  return 1
 return 0

if __name__=='__main__':sys.exit(main())
