"""Adapt existing SDK fakes to the async network boundary."""
from types import SimpleNamespace as NS


def async_client(sync):
    class Stream:
        def __init__(self, source):
            self.source = source
        async def __aiter__(self):
            for chunk in self.source:
                yield chunk
        async def close(self):
            close = getattr(self.source, 'close', None)
            if close:
                close()

    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def __init__(self):
            async def create(**kwargs):
                result = sync.chat.completions.create(**kwargs)
                return Stream(result) if kwargs.get('stream') else result
            self.chat = NS(completions=NS(create=create))
    return Client()
