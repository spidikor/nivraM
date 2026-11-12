"""Explicit protocol migration for old direct-worker positive/error fixtures.

Does not synthesize foreign/replay acceptance. Rendering remains whatever each
original test selected; this helper is not evidence that rendering was run.
"""
import copy
from nivram import import_binding, import_preparation

def prepare_fixture(request):
    request = copy.deepcopy(request)
    if request.get('mode') == 'import':
        request['style'] = import_binding.checked_style(request.get('style'))
        request['importBinding'] = import_binding.capture(request['source'], request['style'])
        return import_preparation._prepare(request)[0]
    return import_preparation._prepare(request)

def bind_fixture_reply(reply, request):
    reply = copy.deepcopy(reply)
    if reply.get('status') == 'ok' and request.get('mode') == 'import':
        from nivram.properties import input_identity
        reply['importReceipt'] = {**copy.deepcopy(request['importBinding']),
            'outputIdentity': input_identity(reply['result']['graph']),
            'outputGraphJson': import_binding.canonical(reply['result']['graph'])}
    return reply
