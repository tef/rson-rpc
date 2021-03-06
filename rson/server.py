import threading
import types
import socket
import traceback
import sys
import inspect

from collections import OrderedDict
from urllib.parse import urljoin, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler

from werkzeug.utils import redirect as Redirect
from werkzeug.wrappers import Request, Response
from werkzeug.exceptions import HTTPException, NotFound, BadRequest, NotImplemented, MethodNotAllowed

from . import format, objects

def funcargs(m):
    args =  m.__code__.co_varnames[:m.__code__.co_argcount]
    args = [a for a in args if not a.startswith('_')]
    if args and args[0] == 'self': args.pop(0)
    return args

def make_resource(obj, url, metadata=None, all_methods=False):
    cls = obj.__class__
    attributes = OrderedDict()
    methods = OrderedDict()
    links = []
    has_key = False

    for k,v in obj.__class__.__dict__.items():
        if v is model_key:
            attributes[k] = obj.id
            has_key = True
            continue
        if not getattr(v, 'rpc', all_methods): continue
        if k.startswith('_'): continue

        if getattr(v, 'safe', False):
            links.append(k)
        else:
            methods[k] = funcargs(v)

    for k,v in obj.__dict__.items():
        if k.startswith('_'): continue
        if has_key and k=='id': continue
        
        attributes[k]= v

    meta = OrderedDict(
        url = url,
        links = links,
        methods = methods,
    )
    if metadata:
        meta.update(metadata)

    return objects.Resource(
        kind = cls.__name__,
        metadata = meta,
        attributes = attributes,
    )


def rpc(safe=False):
    def _fn(fn):
        fn.rpc = True
        fn.safe = safe
        return fn
    return _fn

class RequestHandler:
    pass
    
class FunctionHandler(RequestHandler):
    def __init__(self, url, function):
        self.fn = function
        self.url = url

    def GET(self, path, params):
        return self.fn

    def POST(self, path, params, data):
        return self.fn(**data)

    def link(self):
        if getattr(self.fn, 'safe', False):
            return objects.Link(self.url)
        else:
            return objects.Form(self.url, arguments=funcargs(self.fn))

    def embed(self, o=None):
        return self.link()


class Service:
    def __init__(self):
        pass

    def __getattribute__(self, name):
        if name.startswith('_'):
            return object.__getattribute__(self, name)
        return getattr(object.__getattribute__(self, '__class__'),name)

    class Handler(RequestHandler):
        def __init__(self, url, service):
            self.service = service
            self.url = url

        def GET(self, path, params):
            path = path[len(self.url)+1:]
            if path[:1] == '_': 
                return
            if path:
                return self.service.__dict__[path]()
            return self.service()

        def POST(self, path, paramsm, data):
            path = path[len(self.url)+1:]
            if path[:1] == '_': 
                return
            return self.service.__dict__[path](**data)

        def link(self):
            return objects.Link(self.url)

        def embed(self,o=None):
            if o is None or o is self.service:
                return self.link()
            return make_resource(o, self.url, all_methods=True)

class Token:
    class Handler(RequestHandler):
        def __init__(self, url, view):
            self.view = view
            self.url = url

        def GET(self, path, params):
            path = path[len(self.url)+1:]
            obj =  self.lookup(params)

            if path.startswith('_'): 
                return obj
            elif path:
                return getattr(obj, path)()
            else:
                return obj

        def POST(self, path, params, data):
            if params:
                obj =  self.lookup(params)
                path = path[len(self.url)+1:]
                if path.startswith('_'):
                    return obj
                return getattr(obj, path)(**data)
            return self.view(**data)

        def lookup(self, params):
            params = {key: objects.parse(value) for key,value in params.items() if not key.startswith('_')}
            obj = self.view(**params)
            return obj

        def link(self):
            args = funcargs(self.view.__init__)
            return objects.Form(self.url, arguments=args)

        def embed(self,o=None):
            if o is None or o is self.view:
                return self.link()
            params = {key: objects.dump(value) for key, value in o.__dict__.items()}
            url = "{}?{}".format(self.url, urlencode(params))

            return make_resource(o, url, all_methods=True)

@property
def model_key(self):
    return self.id
@model_key.setter
def model_key(self,value):
    self.id = value

class Collection:
    @staticmethod
    def key():
        return model_key 

    class Handler(RequestHandler):
        def __init__(self, url, model):
            self.model = model
            self.url = url

        def GET(self, path, params):
            method, path = path[len(self.url)+1:], None
            if '/' in method:
                method, path = method.split('/',1)

            if method =='id':
                if '/' in path:
                    id, obj_method = path.split('/',1)
                else:
                    id, obj_method = path, None

                obj = self.lookup(path)
                if obj_method.startswith('_') or not object_method:
                    return obj
                else:
                    return getattr(obj, obj_method)()

            elif method =='list':
                selector = params['selector']
                limit = params.get('limit')
                next = params.get('continue')
                return self.list(selector, limit, next)
            elif method == '':
                return self.link()
            raise Exception(method)

        def POST(self, path, params, data):
            method, path = path[len(self.url)+1:], None
            print(path)
            if '/' in method:
                method, path = method.split('/',1)

            if method == 'id':
                path, obj_method = path.split('/',1)
                obj = self.lookup(path)
                if obj_method.startswith('_') or not object_method:
                    return obj
                else:
                    return getattr(obj, obj_method)(**data)
            elif method == 'new':
                return self.create(**data)
            elif method == 'delete':
                return self.delete(path)
            raise Exception(method)

        def link(self):
            return objects.Collection(
                    kind=self.model.__name__,
                    url=self.url, 
                    arguments=funcargs(self.create))

        def embed(self,o=None):
            if o is None or o is self.model:
                return self.link()
            meta = OrderedDict(
                    id = self.key_for(o),
                    collection = self.url
            )
            url = self.url_for(o)
            return make_resource(o, url, metadata=meta, all_methods=False)

        def url_for(self, o):
            return "{}/id/{}".format(self.url,self.key_for(o))

        # override

        def key_for(self, obj):
            raise Exception('unimplemented')

        def lookup(self, key):
            raise Exception('unimplemented')

        def create(self, **kwargs):
            raise Exception('unimplemented')

        def delete(self, name):
            raise Exception('unimplemented')

        def delete_list(self, selector, limit):
            pass

        def list(self, selector, limit, next):
            raise Exception('unimplemented')

        def watch(self, selector):
            raise Exception('unimplemented')

class Router:
    def __init__(self, prefix="/"):
        self.handlers = OrderedDict()
        self.paths = OrderedDict()
        self.service = None
        if prefix[-1] != '/': prefix += "/"
        self.prefix=prefix

    def add(self, name=None):
        def _add(obj):
            if isinstance(obj, types.FunctionType):
                obj.Handler = FunctionHandler
            self.add_handler(name, obj.Handler, obj)
            return obj

        return _add

    def add_handler(self, name, handler, obj):
        n = obj.__name__ if name is None else name
        self.handlers[n] = handler(self.prefix+n, obj)
        self.paths[obj]=n
        self.service = None

    def index(self):
        if self.service is None:
            attrs = OrderedDict()
            for name,o in self.handlers.items():
                attrs[name] = o.link()
            self.service = objects.Resource('Index',
                metadata={'url':self.prefix},
                attributes=attrs,
            )
        return self.service

    def handle(self, request):
        path = request.path[:]
        if path == self.prefix or path == self.prefix[:-1]:
            out = self.index()
        elif path:
            p = len(self.prefix)
            name = path[p:].split('/',1)[0].split('.',1)[0]
            if name in self.handlers:
                data  = request.data.decode('utf-8')
                if data:
                    args = objects.parse(data)
                else:
                    args = None

                params = request.args

                if request.method == 'GET':
                    out = self.handlers[name].GET(path, params)
                else:
                    out = self.handlers[name].POST(path, params, args)
            else:
                raise NotFound(path)
        
        def transform(o):
            if isinstance(o, type) or isinstance(o, types.FunctionType):
                if o in self.paths:
                    return self.handlers[self.paths[o]].embed(o)
            elif o.__class__ in self.paths:
                return self.handlers[self.paths[o.__class__]].embed(o)

            return o

        if out is None:
            return Response('', status='204 None')

        result = objects.dump(out, transform)
        return Response(result, content_type=objects.CONTENT_TYPE) 
    def app(self):
        return WSGIApp(self.handle)

class WSGIApp:
    def __init__(self, handler):
        self.handler = handler

    def __call__(self, environ, start_response):
        request = Request(environ)
        try:
            response = self.handler(request)
        except (StopIteration, GeneratorExit, SystemExit, KeyboardInterrupt):
            raise
        except HTTPException as r:
            response = r
            self.log_error(r, traceback.format_exc())
        except Exception as e:
            trace = traceback.format_exc()
            self.log_error(e, trace)
            response = self.error_response(e, trace)
        return response(environ, start_response)

    def log_error(self, exception, trace):
        print(trace, file=sys.stderr)

    def error_response(self, exception, trace):
        return Response(trace, status='500 not ok (%s)'%exception)

class QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass

class Server(threading.Thread):
    def __init__(self, app, host="", port=0, request_handler=QuietWSGIRequestHandler):
        threading.Thread.__init__(self)
        self.daemon=True
        self.running = True
        self.server = make_server(host, port, app,
            handler_class=request_handler)

    @property
    def url(self):
        return u'http://%s:%d/'%(self.server.server_name, self.server.server_port)

    def run(self):
        self.running = True
        while self.running:
            self.server.handle_request()

    def stop(self):
        self.running =False
        if self.server and self.is_alive():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(self.server.socket.getsockname()[:2])
                s.send(b'\r\n')
                s.close()
            except IOError:
                import traceback
                traceback.print_exc()
        self.join(5)
