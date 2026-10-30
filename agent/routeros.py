import asyncio
from contextlib import contextmanager
from functools import partial

import routeros_api

import base


class RouterOSError(RuntimeError):
    """An error returned while communicating with the RouterOS API."""


class RouterOSUnavailable(RouterOSError):
    """The RouterOS API could not be reached or authenticated."""


class RouterOSClient:
    """Small async-safe facade over the blocking routeros-api client."""

    def __init__(self, pool_factory=None):
        self._pool_factory = pool_factory or routeros_api.RouterOsApiPool

    @contextmanager
    def _connection(self):
        pool = None
        try:
            pool = self._pool_factory(
                base.ROUTEROS_HOST,
                username=base.ROUTEROS_USERNAME,
                password=base.ROUTEROS_PASSWORD,
                port=base.ROUTEROS_PORT,
                use_ssl=base.ROUTEROS_USE_SSL,
                ssl_verify=base.ROUTEROS_SSL_VERIFY,
                plaintext_login=True,
                auto_reconnect=True,
            )
            yield pool.get_api()
        except RouterOSError:
            raise
        except Exception as exc:
            raise RouterOSUnavailable(str(exc)) from exc
        finally:
            if pool is not None:
                try:
                    pool.disconnect()
                except Exception:
                    pass

    def _call(self, operation, *args, **kwargs):
        with self._connection() as api:
            try:
                return operation(api, *args, **kwargs)
            except RouterOSError:
                raise
            except Exception as exc:
                raise RouterOSError(str(exc)) from exc

    async def call(self, operation, *args, **kwargs):
        return await asyncio.to_thread(partial(self._call, operation, *args, **kwargs))

    async def get(self, path, **query):
        def operation(api):
            return api.get_resource(path).get(**query)

        return await self.call(operation)

    async def add(self, path, **values):
        def operation(api):
            return api.get_resource(path).add(**values)

        return await self.call(operation)

    async def set(self, path, query, **values):
        def operation(api):
            return api.get_resource(path).set(query, **values)

        return await self.call(operation)

    async def remove(self, path, query):
        def operation(api):
            return api.get_resource(path).remove(query)

        return await self.call(operation)

    async def update(self, path, query, **values):
        def operation(api):
            return api.get_resource(path).update(query, **values)

        return await self.call(operation)


client = RouterOSClient()