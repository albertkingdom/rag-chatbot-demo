"""Local/GCP job runner contract without submitting cloud work."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from src import manual_service as jobs

@pytest.mark.parametrize('state,expected',[('queued','queued'),('deferred','queued'),('scheduled','queued'),('started','running'),('finished','succeeded'),('failed','failed'),('stopped','failed'),('canceled','failed')])
def test_local_job_status_enum_normalization(monkeypatch,state,expected):
    from rq.job import Job
    from src import services
    job=MagicMock();job.get_status.return_value=SimpleNamespace(value=state)
    monkeypatch.setattr(Job,'fetch',lambda *args,**kwargs:job)
    monkeypatch.setattr(services,'get_redis_conn',lambda:MagicMock())
    assert jobs.get_sync_status('local-job')['status']==expected

@pytest.mark.parametrize('succeeded,failed,expected',[(0,0,'running'),(1,0,'succeeded'),(0,1,'failed')])
def test_gcp_job_status(monkeypatch,succeeded,failed,expected):
    from google.cloud import run_v2
    execution='projects/test/locations/asia-east1/jobs/sync/executions/run-1'
    client=MagicMock();client.get_execution.return_value=SimpleNamespace(succeeded_count=succeeded,failed_count=failed)
    monkeypatch.setattr(run_v2,'ExecutionsClient',lambda:client)
    encoded=jobs._encode_gcp_job(execution)
    assert jobs.get_sync_status(encoded)['status']==expected
    client.get_execution.assert_called_once_with(name=execution)


def test_gcp_enqueue_and_missing_execution(monkeypatch):
    from google.cloud import run_v2
    from google.api_core.exceptions import NotFound
    monkeypatch.setattr(jobs,'JOB_RUNNER','gcp')
    monkeypatch.setenv('GCP_PROJECT_ID','test')
    execution='projects/test/locations/asia-east1/jobs/sync-job/executions/run-1'
    client=MagicMock();client.run_job.return_value=SimpleNamespace(metadata=SimpleNamespace(name=execution))
    monkeypatch.setattr(run_v2,'JobsClient',lambda:client)
    assert jobs._decode_gcp_job(jobs.enqueue_sync())==execution
    client.run_job.assert_called_once_with(name='projects/test/locations/asia-east1/jobs/sync-job')
    client.get_execution.side_effect=NotFound('missing')
    monkeypatch.setattr(run_v2,'ExecutionsClient',lambda:client)
    with pytest.raises(KeyError): jobs.get_sync_status(jobs._encode_gcp_job(execution))
