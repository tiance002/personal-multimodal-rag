"""Actual local BPE asset, never network or a provider request."""
from dataclasses import replace
from pathlib import Path
from backend.weknora_port.generation.estimator import LocalTextTokenEstimator,BASIS
from backend.weknora_port.models.types import Message,ChatOptions
from backend.weknora_port.generation.features import extract_features

ASSET=Path('D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken')

def test_actual_bpe_full_messages_and_unknown_unsupported(tmp_path):
    assert ASSET.is_file(), 'explicit local asset prerequisite'
    estimator=LocalTextTokenEstimator(ASSET)
    messages=(Message('system','Evidence and instructions'),Message('user','Chinese 中文 and literal <|endoftext|>'))
    estimate=estimator.estimate(messages,ChatOptions())
    assert estimate.tokens>0 and estimate.estimator==BASIS
    longer=estimator.estimate(messages+(Message('user',' extra '*3000),),ChatOptions())
    assert longer.tokens>estimate.tokens
    feature=extract_features('extract source','full prompt',estimated_tokens=longer.tokens,estimator=longer.estimator)
    assert feature.statuses[4]!='UNKNOWN_NOT_INFERRED'
    assert feature.public()['token_basis']=='not_provider_actual'
    assert estimator.estimate((replace(messages[1],images=('public fixture image',)),),ChatOptions()).tokens is None
    assert estimator.estimate((replace(messages[1],multi_content=({'type':'text','text':'x'},)),),ChatOptions()).tokens is None
    assert LocalTextTokenEstimator().estimate(messages,ChatOptions()).tokens is None
    bad=tmp_path/'bad.tiktoken';bad.write_bytes(b'wrong')
    assert LocalTextTokenEstimator(bad).estimate(messages,ChatOptions()).tokens is None
    assert LocalTextTokenEstimator(tmp_path/'missing').estimate(messages,ChatOptions()).tokens is None
