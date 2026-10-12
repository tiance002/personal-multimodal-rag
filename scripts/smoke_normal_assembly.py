"""Ordinary production entry, network disabled by caller, no hidden injections."""
import json


def main():
    try:
        from backend.app.main import app
        readiness = app.state.container.model_readiness()
        assert readiness['status'] == 'READY_CONFIG'
        print(json.dumps(dict(status='CHECKS_PASSED', entry='backend.app.main:app',
            check_kind='REAL_IMAGE_CONFIGURATION_ONLY', provider_calls=0, readiness=readiness)))
    except Exception as error:
        print(json.dumps(dict(status='FAIL', failure_type=type(error).__name__,
            provider_calls=0)))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
