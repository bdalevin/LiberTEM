import functools
import logging

import tornado.util
from dask import distributed as dd

from .base import JobExecutor, JobCancelledError, sync_to_async, AsyncAdapter


log = logging.getLogger(__name__)


class CommonDaskMixin(object):
    def _task_idx_to_workers(self, workers, idx):
        hosts = list(sorted(set(w['host'] for w in workers)))
        host_idx = idx % len(hosts)
        host = hosts[host_idx]
        return [
            w['name']
            for w in workers
            if w['host'] == host
        ]

    def _get_futures(self, job):
        futures = []
        available_workers = self.get_available_workers()
        if len(available_workers) == 0:
            raise RuntimeError("no workers available!")
        for task in job.get_tasks():
            submit_kwargs = {}
            locations = task.get_locations()
            if locations is not None and len(locations) == 0:
                raise ValueError("no workers found for task")
            if locations is None:
                locations = self._task_idx_to_workers(available_workers, task.idx)
            submit_kwargs['workers'] = locations
            futures.append(
                self.client.submit(task, **submit_kwargs)
            )
        return futures

    def get_available_workers(self):
        info = self.client.scheduler_info()
        return [
            {
                'name': worker['name'],
                'host': worker['host'],
            }
            for worker in info['workers'].values()
        ]


class DaskJobExecutor(CommonDaskMixin, JobExecutor):
    def __init__(self, client, is_local=False):
        self.is_local = is_local
        self.client = client
        self._futures = {}

    def run_job(self, job):
        futures = self._get_futures(job)
        self._futures[job] = futures
        for future, result in dd.as_completed(futures, with_results=True):
            if future.cancelled():
                raise JobCancelledError()
            yield result
        del self._futures[job]

    def cancel_job(self, job):
        if job in self._futures:
            futures = self._futures[job]
            self.client.cancel(futures)

    def run_function(self, fn, *args, **kwargs):
        """
        run a callable `fn`
        """
        fn_with_args = functools.partial(fn, *args, **kwargs)
        future = self.client.submit(fn_with_args, priority=1)
        return future.result()

    def map_partitions(self, dataset, fn, fn_kwargs=None):
        if fn_kwargs is None:
            fn_kwargs = {}
        # FIXME: map_partitions should maybe not be part of executor? not sure about right place
        futures = []
        for partition in dataset.get_partitions():
            fn_bound = functools.partial(fn, partition=partition, **fn_kwargs)
            futures.append(
                self.client.submit(fn_bound, workers=partition.get_locations())
            )
        for future, result in dd.as_completed(futures, with_results=True):
            yield result

    def close(self):
        if self.is_local:
            if self.client.cluster is not None:
                try:
                    self.client.cluster.close(timeout=1)
                except tornado.util.TimeoutError:
                    pass
        self.client.close()

    @classmethod
    def connect(cls, scheduler_uri, *args, **kwargs):
        """
        Connect to a remote dask scheduler

        Returns
        -------
        DaskJobExecutor
            the connected JobExecutor
        """
        client = dd.Client(address=scheduler_uri)
        return cls(client=client, is_local=False, *args, **kwargs)

    @classmethod
    def make_local(cls, cluster_kwargs=None, client_kwargs=None):
        """
        Spin up a local dask cluster

        interesting cluster_kwargs:
            threads_per_worker
            n_workers

        Returns
        -------
        DaskJobExecutor
            the connected JobExecutor
        """
        cluster = dd.LocalCluster(**(cluster_kwargs or {}))
        client = dd.Client(cluster, **(client_kwargs or {}))
        return cls(client=client, is_local=True)


class AsyncDaskJobExecutor(AsyncAdapter):
    def __init__(self, wrapped=None, *args, **kwargs):
        if wrapped is None:
            wrapped = DaskJobExecutor(*args, **kwargs)
        super().__init__(wrapped)

    @classmethod
    async def connect(cls, scheduler_uri, *args, **kwargs):
        executor = await sync_to_async(functools.partial(
            DaskJobExecutor.connect,
            scheduler_uri=scheduler_uri,
            *args,
            **kwargs,
        ))
        return cls(wrapped=executor)

    @classmethod
    async def make_local(cls, cluster_kwargs=None, client_kwargs=None):
        executor = await sync_to_async(functools.partial(
            DaskJobExecutor.make_local,
            cluster_kwargs=cluster_kwargs,
            client_kwargs=client_kwargs,
        ))
        return cls(wrapped=executor)
