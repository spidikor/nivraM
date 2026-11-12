"""Owned WebView2 confirmation automation, without replacing window.confirm.

Observe real Page dialog events and answer the exact armed recovery prompt through
the same CoreWebView2 instance. Default dialogs remain enabled. This establishes
dialog dispatch, chosen result and closure; it does not inspect painted pixels.
No remote debugging port, global input or public bridge capability is added.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time

from .acceptance_editing import require
from .acceptance_keys import handles
from .acceptance_click import _WindowsBackend,_browser_pid

EVENTS=('Page.javascriptDialogOpening','Page.javascriptDialogClosed')


def validate_open(value,message,url,session=''):
    require(type(value) is dict and session=='' and value.get('url')==url
        and value.get('message')==message and value.get('type')=='confirm'
        and value.get('hasBrowserHandler') is True
        and value.get('defaultPrompt','')=='', 'recovery_confirmation_identity')


def validate_closed(value,decision,session=''):
    require(type(value) is dict and session=='' and type(value.get('result')) is bool
        and value['result'] is decision and value.get('userInput','')=='',
        'recovery_confirmation_result')


class RecoveryConfirmation:
    def __init__(self,window,url):
        self.control,self.owner,self.handle=handles(window)
        self.backend=_WindowsBackend(_browser_pid(self.control))
        self.url=url
        self.rows=[]
        self.active=None
        self.error=None
        self.receivers=[]
        self.attached=False
        self.closed=False
        self.lock=threading.RLock()
        self.event=threading.Event()

    def _owned(self):
        require(self.backend.process_pinned(self.backend.pid)
            and self.backend.process_pinned(self.backend.browser_pid)
            and self.backend.window(self.owner) and self.backend.window(self.handle)
            and self.backend.window_pid(self.owner)==self.backend.pid
            and self.backend.visible(self.owner), 'recovery_confirmation_owner')
        require(int(self.control.CoreWebView2.BrowserProcessId)==self.backend.browser_pid,
                'recovery_confirmation_browser_changed')
        require(bool(self.control.CoreWebView2.Settings.AreDefaultScriptDialogsEnabled),
                'recovery_confirmation_default_disabled')

    def _ui(self,operation,timeout=3):
        done=threading.Event();result={};lock=threading.Lock()
        deadline=time.monotonic()+timeout
        def start():
            with lock:
                if result.get('abandoned') or time.monotonic()>=deadline:return
                try:
                    self._owned()
                    result['value']=operation()
                except Exception as error:result['error']=error
                finally:done.set()
        if self.control.InvokeRequired:
            from System import Action
            self.control.BeginInvoke(Action(start))
        else:start()
        try:
            require(done.wait(max(0,deadline-time.monotonic())), 'recovery_confirmation_ui_timeout')
            if 'error' in result:raise result['error']
            return result['value']
        finally:
            with lock:result['abandoned']=True

    @staticmethod
    def _settled(task,timeout=3):
        deadline=time.monotonic()+timeout
        while not task.IsCompleted and time.monotonic()<deadline:time.sleep(.005)
        require(task.IsCompleted and not task.IsFaulted and not task.IsCanceled,
                'recovery_confirmation_command_unconfirmed')
        require(json.loads(str(task.Result))=={}, 'recovery_confirmation_command_result')

    def _opening(self,sender,args):
        try:
            self._owned()
            value=json.loads(str(args.ParameterObjectAsJson))
            with self.lock:
                pending=self.active
                require(pending is not None and not pending.get('opened'), 'recovery_confirmation_unarmed')
                validate_open(value,pending['message'],self.url,str(args.SessionId))
                pending.update(opened=True,defaultDialogsEnabled=True,hasBrowserHandler=True,
                    exactPromptVerified=True,ownedControlVerified=True)
                pending['task']=self.control.CoreWebView2.CallDevToolsProtocolMethodAsync(
                    'Page.handleJavaScriptDialog',json.dumps({'accept':pending['decision']}))
        except Exception as error:
            self.error=type(error).__name__
            self.event.set()

    def _closing(self,sender,args):
        try:
            self._owned()
            value=json.loads(str(args.ParameterObjectAsJson))
            with self.lock:
                pending=self.active
                require(pending is not None and pending.get('opened') is True,
                        'recovery_confirmation_unexpected_close')
                validate_closed(value,pending['decision'],str(args.SessionId))
                pending['actualResultVerified']=True
                pending['closed']=True
                self.event.set()
        except Exception as error:
            self.error=type(error).__name__
            self.event.set()

    def attach(self):
        require(not self.attached and not self.closed, 'recovery_confirmation_already_attached')
        def install():
            for event,handler in zip(EVENTS,(self._opening,self._closing)):
                receiver=self.control.CoreWebView2.GetDevToolsProtocolEventReceiver(event)
                receiver.DevToolsProtocolEventReceived+=handler
                self.receivers.append((receiver,handler))
            self.attached=True
            return self.control.CoreWebView2.CallDevToolsProtocolMethodAsync('Page.enable','{}')
        self._settled(self._ui(install))

    def arm(self,message,decision):
        require(type(message) is str and 1<=len(message)<=1000 and type(decision) is bool,
                'recovery_confirmation_arguments')
        with self.lock:
            require(self.attached and not self.closed and self.active is None and self.error is None,
                    'recovery_confirmation_not_ready')
            self.event.clear()
            self.active=dict(message=message,decision=decision,opened=False,closed=False,
                promptSha256=hashlib.sha256(message.encode()).hexdigest())

    def finish(self):
        require(self.event.wait(5) and self.error is None, 'recovery_confirmation_not_closed')
        with self.lock:
            row=self.active
            require(row is not None and row.get('closed') is True, 'recovery_confirmation_incomplete')
            task=row.get('task')
        require(task is not None, 'recovery_confirmation_no_command')
        self._settled(task)
        with self.lock:
            row['commandCompleted']=True
            saved={k:v for k,v in row.items() if k not in ('message','task')}
            self.rows.append(saved)
            self.active=None
            return copy.deepcopy(saved)

    def close(self):
        if self.closed:return
        try:
            def detach():
                for receiver,handler in self.receivers:
                    receiver.DevToolsProtocolEventReceived-=handler
                self.receivers.clear()
                self.attached=False
                return self.control.CoreWebView2.CallDevToolsProtocolMethodAsync('Page.disable','{}')
            if self.receivers:self._settled(self._ui(detach))
            require(self.active is None and self.error is None, 'recovery_confirmation_pending_on_close')
        finally:
            self.closed=True
            self.backend.close()
