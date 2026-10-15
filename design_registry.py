"""
Per-wallet DesignRegistry anchoring on BOT Chain.

Each customer's OWN wallet signs and sends its own anchorDesign()
transaction. The server never holds or uses a signing key for this any
more (the old shared ANCHOR_WALLET_PRIVATE_KEY flow, where one server
wallet signed for everybody, is gone - so `submitter` in the contract
is now the customer's wallet, and the customer pays the gas).

Flow, split across two steps because a server can't sign for a user:

  1. build_anchor_tx()   - server builds the unsigned call (to / data /
     chainId) for the signed-in wallet. The browser wallet signs and
     sends it (eth_sendTransaction).
  2. verify_anchor_tx()  - browser posts back the tx hash; the server
     checks on-chain that it was sent BY that wallet, TO the registry,
     for THIS job's id + parameters hash, and didn't revert. Only then
     is it stored as the job's anchor_tx.

Anchoring stays optional: a customer who skips or rejects the wallet
prompt still keeps their paid export. Nothing here raises into the
export path.
"""

from __future__ import annotations

import json
from pathlib import Path

from web3 import Web3

import db
from config import settings
from logging_config import get_logger

logger = get_logger(__name__)

# BOT Chain is a Proof-of-Authority chain: its blocks carry an extraData
# field longer than the 32 bytes stock web3.py expects, which raises
# ExtraDataLengthError on any call that touches block-header data -
# including build_transaction()'s own fee estimation, below. See
# scripts/deploy_design_registry.py's identical note; that script
# injects this same middleware for the exact same build_transaction()
# call pattern on the exact same chain, but this module - the one that
# actually runs on every STEP export, not just once at deploy time -
# never did. Without it, anchor_design() fails on every call, gets
# swallowed by the bare except below (by design, so anchoring failures
# never break a paid export), and shows up nowhere but the logs.
try:
    from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware
except ImportError:
    from web3.middleware import geth_poa_middleware as _poa_middleware

REPO_ROOT = Path(__file__).resolve().parent
ABI_PATH = REPO_ROOT / "contracts" / "DesignRegistry.abi.json"

_EMBEDDED_ABI = [
    {
        "type": "function", "name": "anchorDesign", "stateMutability": "nonpayable",
        "inputs": [
            {"name": "jobId", "type": "bytes32"},
            {"name": "partType", "type": "string"},
            {"name": "parametersHash", "type": "bytes32"},
            {"name": "parameters", "type": "string"},
            {"name": "templateVersion", "type": "string"},
            {"name": "outputHash", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "type": "function", "name": "getDesign", "stateMutability": "view",
        "inputs": [{"name": "jobId", "type": "bytes32"}],
        "outputs": [{
            "name": "", "type": "tuple",
            "components": [
                {"name": "partType", "type": "string"},
                {"name": "parametersHash", "type": "bytes32"},
                {"name": "templateVersion", "type": "string"},
                {"name": "outputHash", "type": "bytes32"},
                {"name": "timestamp", "type": "uint256"},
                {"name": "submitter", "type": "address"},
            ],
        }],
    },
    {
        "type": "function", "name": "isAnchored", "stateMutability": "view",
        "inputs": [{"name": "jobId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function", "name": "designs", "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [
            {"name": "partType", "type": "string"},
            {"name": "parametersHash", "type": "bytes32"},
            {"name": "templateVersion", "type": "string"},
            {"name": "outputHash", "type": "bytes32"},
            {"name": "timestamp", "type": "uint256"},
            {"name": "submitter", "type": "address"},
        ],
    },
    {
        "type": "event", "name": "DesignAnchored", "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "jobId", "type": "bytes32"},
            {"indexed": False, "name": "partType", "type": "string"},
            {"indexed": False, "name": "parametersHash", "type": "bytes32"},
            {"indexed": False, "name": "parameters", "type": "string"},
            {"indexed": False, "name": "templateVersion", "type": "string"},
            {"indexed": False, "name": "outputHash", "type": "bytes32"},
            {"indexed": False, "name": "timestamp", "type": "uint256"},
            {"indexed": False, "name": "submitter", "type": "address"},
        ],
    },
]

_abi_cache = None


def _load_abi():
    """Embedded ABI is the default so a live contract works without any
    committed file. contracts/DesignRegistry.abi.json, if present, is an
    optional override."""
    global _abi_cache
    if _abi_cache is None:
        _abi_cache = _EMBEDDED_ABI
        if ABI_PATH.exists():
            try:
                _abi_cache = json.loads(ABI_PATH.read_text())
            except Exception:  # noqa: BLE001
                logger.exception("bad ABI override file, using embedded ABI")
    return _abi_cache


def _registry_address() -> str:
    """Registry address with stray whitespace removed (a pasted Railway
    env value often carries a trailing space/newline)."""
    return (settings.DESIGN_REGISTRY_ADDRESS or "").strip()


def anchoring_status() -> dict:
    """Which pieces are configured. Never exposes secret values."""
    addr = _registry_address()
    return {
        "registry_address": bool(addr),
        "registry_address_valid": bool(addr) and Web3.is_address(addr),
        "rpc_url": bool(settings.botchain_rpc_url),
        "environment": settings.BOTCHAIN_ENVIRONMENT,
    }


def log_anchoring_status() -> None:
    st = anchoring_status()
    missing = [k for k in ("registry_address", "rpc_url") if not st[k]]
    if st["registry_address"] and not st["registry_address_valid"]:
        missing.append("registry_address_valid")
    if missing:
        logger.warning("anchoring DISABLED, missing/invalid: %s", ", ".join(missing), extra=st)
    else:
        logger.info("anchoring ENABLED on %s", st["environment"], extra=st)


def anchoring_missing() -> list[str]:
    """Names of the server settings that stop anchoring from working
    (empty list == anchoring is available). Names only, never values."""
    st = anchoring_status()
    missing = []
    if not st["registry_address"]:
        missing.append("DESIGN_REGISTRY_ADDRESS")
    elif not st["registry_address_valid"]:
        missing.append("DESIGN_REGISTRY_ADDRESS (not a valid 0x address)")
    if not st["rpc_url"]:
        missing.append("BOTCHAIN_*_RPC_URL")
    return missing


def anchoring_available() -> bool:
    """True when registry address and RPC URL are set. No server key is
    needed - users sign with their own wallets. The ABI is embedded."""
    return not anchoring_missing()


def _contract(w3):
    return w3.eth.contract(
        address=Web3.to_checksum_address(_registry_address()),
        abi=_load_abi(),
    )


def _canonical_params(parameters: dict) -> str:
    return json.dumps(parameters, sort_keys=True, separators=(",", ":"))


def _job_id_bytes32(job_id: str) -> bytes:
    return bytes(Web3.keccak(text=job_id))


def build_anchor_tx(
    job_id: str, part_type: str, parameters: dict, output_path: Path, from_address: str
) -> dict:
    """Unsigned anchorDesign() call for the given wallet to sign and send
    itself. Pure encoding - no server key, no nonce/gas (the user's
    wallet fills those in). Raises on bad config; callers turn that
    into an HTTP error."""
    w3 = Web3(Web3.HTTPProvider(settings.botchain_rpc_url))
    contract = _contract(w3)
    canonical = _canonical_params(parameters)
    fn = contract.functions.anchorDesign(
        _job_id_bytes32(job_id),
        part_type,
        bytes(Web3.keccak(text=canonical)),
        canonical,
        settings.template_version,
        bytes(Web3.keccak(output_path.read_bytes())),
    )
    return {
        "from": Web3.to_checksum_address(from_address),
        "to": Web3.to_checksum_address(_registry_address()),
        "data": fn._encode_transaction_data(),
        "value": "0x0",
        "chainId": settings.botchain_chain_id_hex,
    }


def verify_anchor_tx(
    job_id: str, parameters: dict, wallet_address: str, tx_hash: str
) -> tuple[str, str | None]:
    """Check a wallet-submitted anchor tx against the chain.

    Returns (status, reason): status is "ok" (tx found, matches, and
    either pending or successful), "pending" (node doesn't know the tx
    yet - retry shortly) or "invalid" (wrong sender / contract / job /
    params, or it reverted). Never trusts the client's say-so."""
    try:
        w3 = Web3(Web3.HTTPProvider(settings.botchain_rpc_url))
        w3.middleware_onion.inject(_poa_middleware, layer=0)
        try:
            tx = w3.eth.get_transaction(tx_hash)
        except Exception:  # noqa: BLE001 - web3 raises TransactionNotFound
            return "pending", "Transaction not visible on the node yet"

        if str(tx["from"]).lower() != wallet_address.lower():
            return "invalid", "Transaction was not sent by your wallet"
        if not tx.get("to") or str(tx["to"]).lower() != _registry_address().lower():
            return "invalid", "Transaction is not a call to the DesignRegistry"

        contract = _contract(w3)
        try:
            fn, args = contract.decode_function_input(tx["input"])
        except Exception:  # noqa: BLE001
            return "invalid", "Transaction data is not an anchorDesign call"
        if getattr(fn, "fn_name", None) != "anchorDesign":
            return "invalid", "Transaction data is not an anchorDesign call"
        if bytes(args["jobId"]) != _job_id_bytes32(job_id):
            return "invalid", "Transaction anchors a different job"
        expected = bytes(Web3.keccak(text=_canonical_params(parameters)))
        if bytes(args["parametersHash"]) != expected:
            return "invalid", "Transaction parameters do not match this job"

        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:  # noqa: BLE001 - still pending
            return "ok", None
        if receipt["status"] != 1:
            return "invalid", "Transaction reverted on-chain"
        return "ok", None
    except Exception:  # noqa: BLE001
        logger.exception("anchor verification failed", extra={"job_id": job_id})
        return "pending", "Could not verify right now"
