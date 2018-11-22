import numpy as np
from libertem.viz import visualize_simple
from .base import BaseAnalysis, AnalysisResultSet, AnalysisResult
from libertem.job.masks import ApplyMasksJob


class BaseMasksAnalysis(BaseAnalysis):
    """
    base class for any masks-based analysis; you only need to implement
    ``get_results`` and ``get_mask_factories``
    """

    @property
    def dtype(self):
        return np.dtype(self.dataset.dtype).kind == 'f' and self.dataset.dtype or "float32"

    def get_job(self):
        mask_factories = self.get_mask_factories()
        job = ApplyMasksJob(dataset=self.dataset, mask_factories=mask_factories)
        return job

    def get_mask_factories(self):
        raise NotImplementedError()


class MasksAnalysis(BaseMasksAnalysis):
    def get_mask_factories(self):
        return self.parameters['factories']

    def get_results(self, job_results):
        return AnalysisResultSet([
            AnalysisResult(raw_data=mask_result, visualized=visualize_simple(mask_result),
                           key="mask_%d" % i, title="mask %d" % i,
                           desc="intensity for mask %d" % i)
            for i, mask_result in enumerate(job_results)
        ])
