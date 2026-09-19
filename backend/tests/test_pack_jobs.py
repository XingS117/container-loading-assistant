import asyncio
import threading

import httpx

from app import main
from app.packing import pack_order


def payload():
    return {'container':{'id':'c','name':'c','inner_length_mm':2000,'inner_width_mm':1000,'inner_height_mm':1000,'door_width_mm':1000,'door_height_mm':1000,'max_payload_g':1000000},
            'cargo_items':[{'id':'a','sku':'a','name':'a','kind':'carton','length_mm':500,'width_mm':500,'height_mm':500,'weight_g':1000,'quantity':2,'allowed_orientations':['LWH'],'stackable':False,'max_layers':1}]}


def test_job_returns_before_solver_finishes_and_reports_actual_stage(monkeypatch):
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async def calculate(request):
            started.set()
            await finish.wait()
            return pack_order(request)
        monkeypatch.setattr(main,'run_pack_calculation',calculate)
        monkeypatch.setattr(main,'load_ai_layout_hint_diagnostic',lambda *a,**kw:main.LayoutHintResult(None))
        monkeypatch.setattr(main,'pack_slots',threading.BoundedSemaphore(1))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            submitted=await client.post('/api/v1/pack/jobs',json=payload(),headers={'X-AI-API-Key':'secret-test-key'})
            assert submitted.status_code==202
            job=submitted.json()['job_id']
            await asyncio.wait_for(started.wait(),2)
            state=await client.get(f'/api/v1/pack/jobs/{job}')
            assert state.json()['phase']=='solving'
            assert 'secret-test-key' not in state.text and 'cargo_items' not in state.text
            assert state.headers['cache-control']=='no-store'
            assert (await client.post('/api/v1/pack',json=payload())).status_code==503
            assert (await client.post('/api/v1/pack/jobs',json=payload())).status_code==503
            finish.set()
            for _ in range(100):
                state=await client.get(f'/api/v1/pack/jobs/{job}')
                if state.json()['phase']=='complete': break
                await asyncio.sleep(.01)
            assert state.json()['phase']=='complete'
            assert len(state.json()['result']['solutions'])==3
            assert main.pack_slots.acquire(blocking=False)
            main.pack_slots.release()
    asyncio.run(scenario())


def test_job_capacity_expiry_and_shutdown_do_not_leak_slots():
    from app.pack_jobs import PackJobs
    from fastapi.responses import JSONResponse
    async def scenario():
        jobs=PackJobs(limit=1,ttl_seconds=900)
        releases=[]
        async def pending(phase):
            await asyncio.Event().wait()
        first=jobs.start(pending,lambda:releases.append('first'))
        try:
            jobs.start(pending,lambda:releases.append('unexpected'))
            assert False,'running task must not be evicted'
        except RuntimeError:
            pass
        await jobs.close()
        assert releases==['first']
        assert first.phase=='failed' and first.error['code']=='JOB_EXPIRED'
        async def timeout(phase):
            return JSONResponse(status_code=504,content={'error':{'code':'CALCULATION_TIMEOUT','message':'timeout'}})
        second=jobs.start(timeout,lambda:releases.append('second'))
        await asyncio.gather(*jobs.tasks)
        assert jobs.get(first.id) is None
        assert second.error['code']=='CALCULATION_TIMEOUT'
        second.finished-=901
        assert jobs.get(second.id) is None
        assert releases==['first','second']
    asyncio.run(scenario())


def test_failed_job_retains_error_category_and_releases_slot(monkeypatch):
    async def scenario():
        async def fail(_request):
            raise main.PackingFailure('INVALID_LOCKED_LAYOUT','locked invalid','fix input')
        monkeypatch.setattr(main,'run_pack_calculation',fail)
        monkeypatch.setattr(main,'load_ai_layout_hint_diagnostic',lambda *a,**kw:main.LayoutHintResult(None))
        monkeypatch.setattr(main,'pack_slots',threading.BoundedSemaphore(1))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            submitted=await client.post('/api/v1/pack/jobs',json=payload())
            assert submitted.status_code==202
            for _ in range(100):
                state=await client.get('/api/v1/pack/jobs/'+submitted.json()['job_id'])
                if state.json()['phase']=='failed': break
                await asyncio.sleep(.01)
            assert state.json()['error']['code']=='INVALID_LOCKED_LAYOUT'
            assert state.json()['http_status']==422
            assert main.pack_slots.acquire(blocking=False)
            main.pack_slots.release()
            assert (await client.get('/api/v1/pack/jobs/unknown')).status_code==404
    asyncio.run(scenario())
