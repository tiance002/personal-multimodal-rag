"""Concrete gate identities supplied by the trusted composition root.

No registration, egress permission, ledger access or accounting occurs here.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class V6LedgerAuthority:
    """Pinned migration manifest, not a new grant or a ledger initializer.

    Load explicitly at the authorized evaluation entry. The application gate
    rechecks it under its existing stable lock; no discovery/default override.
    """
    manifest_path: str
    manifest_sha256: str
    authority_id: str
    ledger_path: str
    legacy_state_sha256: str
    initial_v6_attempts: tuple
    source_baseline_sha256: str
    execution_kind: str
    marker_sha256: str | None = None
    authority_epoch: int | None = None
    source_fence_marker_path: str | None = None
    source_fence_marker_sha256: str | None = None

    @classmethod
    def from_manifest(cls,path,*,expected_sha256,expected_authority_id):
        from pathlib import Path
        import hashlib,json,re,os
        try:
            raw=Path(path).read_bytes()
            if not re.fullmatch('[0-9a-f]{64}',expected_sha256) or hashlib.sha256(raw).hexdigest()!=expected_sha256:
                raise ValueError()
            manifest=json.loads(raw)
            data=manifest['v6_deepseek_authority']
            if (type(data) is not dict or data['schema_version']!=1 or type(data['schema_version']) is not int
                    or data['status']!='ACTIVE_SINGLE_WRITER' or data['authority_id']!=expected_authority_id
                    or type(expected_authority_id) is not str or not expected_authority_id
                    or data['execution_kind'] not in ('REAL','SIMULATED')
                    or data['execution_kind']=='REAL' and (os.name!='posix' or data['platform']!='linux')
                    or type(data['source_writer_fenced']) is not bool or not data['source_writer_fenced']
                    or type(data['initial_v6_attempts']) is not list
                    or not all(type(r) is dict and r.get('status') in ('ok','unknown','truncated','not_sent') for r in data['initial_v6_attempts'])
                    or any(type(data[k]) is not str or not re.fullmatch('[0-9a-f]{64}',data[k])
                        for k in ('legacy_state_sha256','source_baseline_sha256','source_snapshot_sha256'))
                    or type(data['ledger_path']) is not str or not Path(data['ledger_path']).is_absolute()
                    or '..' in Path(data['ledger_path']).parts or not Path(data['ledger_path']).is_file()):
                raise ValueError()
            marker_sha=epoch=fence_path=fence_sha=None
            if data['execution_kind']=='REAL' or 'authority_epoch' in data:
                epoch=data['authority_epoch'];marker_sha=data['marker_sha256']
                fence_path=data['source_fence_marker_path'];fence_sha=data['source_fence_marker_sha256']
                if (type(epoch) is not int or epoch<=0 or any(type(h) is not str or not re.fullmatch('[0-9a-f]{64}',h) for h in (marker_sha,fence_sha))
                        or not Path(fence_path).is_absolute() or not Path(fence_path).is_file()):raise ValueError()
                fence_raw=Path(fence_path).read_bytes();fence=json.loads(fence_raw)
                active_raw=Path(str(Path(data['ledger_path']).resolve())+'.authority.json').read_bytes();active=json.loads(active_raw)
                if data['execution_kind']=='REAL':
                    from pathlib import PureWindowsPath
                    from backend.app.application.session_attempts import CANONICAL_LEDGER
                    if PureWindowsPath(fence.get('ledger_path',''))!=PureWindowsPath(str(CANONICAL_LEDGER)):raise ValueError()
                if (hashlib.sha256(fence_raw).hexdigest()!=fence_sha or hashlib.sha256(active_raw).hexdigest()!=marker_sha
                        or fence.get('status')!='FENCED' or type(fence.get('epoch')) is not int or fence['epoch']!=epoch
                        or fence.get('authority_id')!=expected_authority_id or type(fence.get('schema_version')) is not int or fence.get('schema_version')!=1
                        or active.get('status')!='ACTIVE_SINGLE_WRITER' or type(active.get('epoch')) is not int or active['epoch']!=epoch
                        or active.get('authority_id')!=expected_authority_id or type(active.get('schema_version')) is not int or active.get('schema_version')!=1
                        or active.get('ledger_path')!=str(Path(data['ledger_path']).resolve())
                        or active.get('source_fence_marker_sha256')!=fence_sha):raise ValueError()
            return cls(str(Path(path).resolve()),expected_sha256,expected_authority_id,str(Path(data['ledger_path']).resolve()),
                data['legacy_state_sha256'],tuple(data['initial_v6_attempts']),data['source_baseline_sha256'],data['execution_kind'],
                marker_sha,epoch,fence_path,fence_sha)
        except (OSError,ValueError,TypeError,KeyError):
            from backend.app.ports.session_attempts import AttemptDenied
            raise AttemptDenied('V6_AUTHORITY_MANIFEST_INVALID') from None

    def revalidate(self):
        current=type(self).from_manifest(self.manifest_path,expected_sha256=self.manifest_sha256,
                                        expected_authority_id=self.authority_id)
        if type(self) is not V6LedgerAuthority or current!=self:
            from backend.app.ports.session_attempts import AttemptDenied
            raise AttemptDenied('V6_AUTHORITY_CHANGED')

@dataclass(frozen=True)
class DeepSeekGateTypes:
    session: type
    validation: type
    rag: type
    static_pair: type
    immutable_batch: type
    product: type
    product_receipt: type
    v6_owner_activity: type | None = None

    @property
    def supported(self) -> tuple[type, ...]:
        base = (self.session, self.validation, self.rag, self.static_pair,
                self.immutable_batch, self.product)
        return base + ((self.v6_owner_activity,) if self.v6_owner_activity is not None else ())
