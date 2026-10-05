"""Netikri PySide6 moduliai, kad updater.py logiką būtų galima testuoti be Qt."""
import sys
import types


class _Signal:
    def __init__(self, *a, **k):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *a):
        for fn in self._slots:
            fn(*a)


class _Dummy:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Dummy()

    def __call__(self, *a, **k):
        return _Dummy()

    def __or__(self, other):
        return self

    __and__ = __ror__ = __rand__ = __or__

    def __invert__(self):
        return self


class _QThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        self.run()

    def isRunning(self):
        return False

    def wait(self, *a):
        return True


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    m.__getattr__ = lambda attr: _Dummy
    return m


def install():
    if "PySide6" in sys.modules and getattr(sys.modules["PySide6"], "_fake", False):
        return
    core = _module("PySide6.QtCore", Signal=_Signal, QThread=_QThread, QTimer=_Dummy, Qt=_Dummy())
    gui = _module("PySide6.QtGui")
    widgets = _module("PySide6.QtWidgets", QDialog=_Dummy, QApplication=_Dummy)
    svg = _module("PySide6.QtSvg")
    pkg = _module("PySide6", QtCore=core, QtGui=gui, QtWidgets=widgets, QtSvg=svg, _fake=True)
    sys.modules.update({
        "PySide6": pkg, "PySide6.QtCore": core, "PySide6.QtGui": gui, "PySide6.QtWidgets": widgets,
        "PySide6.QtSvg": svg,
    })
