# Configurable model-role storage checkpoint

`storage/configuration.py` supplies a private five-role aggregate and a public masked DTO, matching the React contract: embedding/rerank/cheap/expensive/vlm each carries a configured provider, model, base URL and write-only API key. It introduces no tokenization/principal/ledger settings form and no literal Xing/DeepSeek requirement. Protocol and capacity validation remains delegated to the fixed model catalogue; this store cannot invent capacity values.

Blank keys preserve the existing credential. A changed provider/endpoint requires an explicit key, preventing inadvertent credential transfer. Revision conflicts, repeated/missing roles and internal fields reject before a configuration write. Provider-specific parameters are not carried to a changed model/provider. This is the user-requested configuration interface over the neutral provider mechanisms, not a new RAG policy.

Command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_model_configuration.py -q -p no:cacheprovider
```

Exit **0**, **4 passed in 0.22s**, scripted SQL/offline checks only. `006_configuration.sql`, its manifest and explicit isolated identity-checked applicator are prepared. Actual migration/configuration round-trip **NOT RUN** at this checkpoint. API facade, role registry/capacity catalogue integration and full main-chain acceptance remain pending. No real credential or environment file was read; test secrets are labelled synthetic.

An associated transport correction preserves unknown budget occupancy after a locally observed dispatch even if the propagated ProviderError has its default `sent=False`. Command `python -m pytest --noconftest tests/weknora_port/test_transport_occupancy.py tests/weknora_port/test_provider_transport.py -q -p no:cacheprovider` using the same Python exited **0**, **22 passed in 0.69s**. This mock-network test is not proof of actual provider usage or settlement.
