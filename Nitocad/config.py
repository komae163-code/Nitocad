"""
Centralized application configuration.

Every environment variable the project reads used to be a scattered
`os.getenv(...)` call in db.py, storage.py, deepseek_parser.py, security.py,
and web_app.py, each with its own inline default and no validation - a
typo'd variable name silently becomes "not configured" instead of a
startup error, and nothing documents the full set of knobs in one place.

This module is the single source of truth: one `Settings` object, built
once at import time via `pydantic-settings`, validated at process start
(fails fast and loud instead of failing confusingly at request time), and
every other module imports `settings` from here instead of calling
`os.getenv` directly.

Existing env var names are preserved exactly (DB_PATH, DEEPSEEK_API_KEY,
R2_ACCOUNT_ID, etc.) so this is a drop-in change - no .env file needs to
change, only the code that reads it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- App / environment -------------------------------------------
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    # --- Server ---------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # Comma-separated in the env var; parsed into a list below. Tighten
    # this to your real frontend domain(s) before going to production -
    # "*" (the old hardcoded default) allows any site to call the API
    # from a browser, which is fine for local dev only.
    CORS_ORIGINS: str = "*"
    # HMAC key for the short-lived signed export-download links
    # (POST /export/{fmt}/{job_id}/link). Optional: unset => a random
    # per-process key (fine for one instance; set it for multi-replica).
    DOWNLOAD_LINK_SECRET: str | None = None
    # Turns on /admin/stats and /admin/dashboard (owner-only usage numbers, see
    # admin_stats.py). Unset => those routes answer 404. Use a long random value.
    ADMIN_TOKEN: str | None = None

    # --- Persistence ------------------------------------------------
    DB_PATH: str = "nl_to_cad.db"
    OUTPUT_DIR: str = "./output"

    # --- Rate limiting (slowapi / limits syntax, e.g. "20/minute") ---
    RATE_LIMIT_GENERATE: str = "20/minute"
    # Civil calculations (terrain, earthworks, hydraulics, wall checks). No credit
    # is charged, so the limit is what stops someone using them to burn CPU.
    RATE_LIMIT_CIVIL: str = "60/minute"
    CIVIL_MAX_SURVEY_POINTS: int = 3000

    # File upload and analysis in CAD Studio (STEP/IGES/BREP, PDF, images). Free for now,
    # so these limits are what protect the server and the vision model bill.
    FILE_MAX_UPLOAD_MB: int = 15
    RATE_LIMIT_FILES: str = "30/hour"
    FILE_DAILY_LIMIT_PER_ACCOUNT: int = 60
    FILE_ANALYSIS_TIMEOUT_SECONDS: float = 40.0
    # Files kept so they can be edited (import_store.py). Newest kept per account; a file that a
    # design still uses is never deleted to make room.
    FILE_MAX_IMPORTS_PER_ACCOUNT: int = 40
    FILE_MAX_IMPORT_BYTES: int = 20 * 1024 * 1024        # after compression
    # Vision model for images and PDF pages. Any provider with an OpenAI-compatible
    # chat API that accepts images works. Leave VISION_API_KEY unset to switch image
    # reading off (CAD files and PDF text still work).
    VISION_API_KEY: str | None = None
    VISION_BASE_URL: str | None = None
    VISION_MODEL: str | None = None
    VISION_TIMEOUT_SECONDS: float = 60.0
    # RATE_LIMIT_KEY_ISSUE removed - it rate-limited the old
    # POST /api/keys/generate endpoint, retired along with the whole
    # API-key system (see web_app.py's note where that endpoint used
    # to live, and wallet_auth.py). POST /auth/nonce and /auth/verify
    # use their own inline "20/minute" limit instead.

    # --- DeepSeek parser ------------------------------------------------
    # Railway's actual service variables are named LLM_API_KEY/LLM_BASE_URL/
    # LLM_MODEL, not DEEPSEEK_*, leftover from an earlier provider-agnostic
    # naming convention (predates this file's DEEPSEEK_* names, which the
    # module docstring above claims are the "existing" names - they weren't,
    # in this deployment). Since case_sensitive=True and there's no fuzzy
    # matching, DEEPSEEK_API_KEY silently resolved to None the whole time,
    # no error, no warning, just a permanent silent fallback to the regex
    # parser. Accept both naming conventions instead of picking one and
    # requiring a Railway dashboard rename.
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_DEFAULT_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_TIMEOUT_SECONDS: float = 20.0
    LLM_API_KEY: str | None = None
    LLM_BASE_URL: str | None = None
    LLM_MODEL: str | None = None

    # --- Prompt-refinement chat (prompt_refine.py) -------------------------
    # A perk for wallets currently sitting on a bulk-pack-sized credit
    # balance, not a separate purchase: a chat that helps refine wording
    # before spending a credit on the real generation, DeepSeek-only,
    # never touching the CAD engine. Gated on CURRENT balance, not a
    # permanent "ever bought a pack" flag - see the project's own
    # decision on this. BULK_TIER_CREDIT_THRESHOLD is deliberately a
    # plain number against the existing wallet_credits balance rather
    # than a new "how was this credit acquired" column: 100 credits is
    # comfortably more than anyone would realistically top up one credit
    # at a time (BOTCHAIN_CREDIT_PRICE_BOT's use case), so it reads as
    # "clearly still sitting on a chunk of a pack_1000/pack_10000
    # purchase" without needing to track purchase provenance at all.
    BULK_TIER_CREDIT_THRESHOLD: int = 100
    PROMPT_REFINE_MODEL: str = "deepseek-v4-flash"
    PROMPT_REFINE_MAX_HISTORY_MESSAGES: int = 20
    PROMPT_REFINE_MAX_MESSAGE_CHARS: int = 2000
    # Own rate limit, separate from RATE_LIMIT_GENERATE - this costs real
    # DeepSeek tokens per call with no BOT payment attached to absorb
    # abuse the way /generate's payment requirement naturally does.
    RATE_LIMIT_PROMPT_REFINE: str = "20/hour"

    # --- CAD Studio chats (chat_api.py / chat_service.py) -----------------
    # Preview first, pay when you generate. Every message in the studio builds a
    # free preview (STL only), so fixing a wrong dimension is free. Credits are spent
    # once per version, when the person presses Generate to unlock the full files
    # (STEP, IGES, DXF, PDF), and CHAT_ANSWER_CREDITS is spent when an AI plan answers
    # a question instead of building something.
    #
    # There is no daily message or generation limit. Credits are the meter, and Pro and
    # Max also have their weekly allowance. The AI_MESSAGES_PER_DAY_* settings below are
    # an optional safety valve: 0 means no limit (the default). If free previews ever cost
    # too much, set one to a number in the environment and it applies per AI tier per wallet
    # over a rolling 24 hours, with no code change.
    #
    # Studio AI tiers (ai_models.py). The AI only coordinates: it reads the chat and
    # writes the part spec, and the CadQuery templates build the solid. A stronger
    # model plans better, it does not draw anything. Each tier has a price in credits
    # for pressing Generate.
    AI_MESSAGES_PER_DAY_BASIC: int = 0
    AI_MESSAGES_PER_DAY_PLUS: int = 0
    AI_MESSAGES_PER_DAY_ADVANCED: int = 0
    # The same valve for Pro and Max accounts.
    AI_MESSAGES_PER_DAY_PAID_BASIC: int = 0
    AI_MESSAGES_PER_DAY_PAID_PLUS: int = 0
    AI_MESSAGES_PER_DAY_PAID_ADVANCED: int = 0
    # Engine: no AI at all. The built-in rules parser reads the message and goes straight to
    # the CAD engine, so it costs us nothing to plan and has no message limit.
    GENERATE_CREDITS_ENGINE: int = 2
    GENERATE_CREDITS_BASIC: int = 10
    GENERATE_CREDITS_PLUS: int = 25
    GENERATE_CREDITS_ADVANCED: int = 40
    # One manufacturability check (POST /chats/{id}/parts/{part}/checks): paid once per part version and process, then free to reopen
    CHECK_CREDITS: int = 5
    # Plus tier, GPT. Both the key and the model name must be set for it to show up.
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str | None = None
    # Plus tier, Qwen, through Alibaba Cloud's OpenAI-compatible endpoint. Check the base
    # URL for your region in the Alibaba Cloud Model Studio docs.
    QWEN_API_KEY: str | None = None
    QWEN_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str | None = None
    # Advanced tier, Claude.
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"
    AI_TIMEOUT_SECONDS: float = 30.0
    CHAT_MAX_HISTORY_MESSAGES: int = 30
    CHAT_MAX_CHATS_PER_USER: int = 200
    # Pro and Max have no chat or project limit in practice. This is a guard against runaway
    # automation, far above anything a person reaches by hand.
    CHAT_MAX_CHATS_PAID: int = 10000
    CHAT_MAX_MESSAGES_PER_CHAT: int = 400
    CHAT_MAX_MESSAGE_CHARS: int = 2000
    CHAT_PLANNER_MODEL: str | None = None  # defaults to DEEPSEEK_DEFAULT_MODEL

    # --- Cloudflare R2 storage (optional; falls back to local disk) ----
    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET_NAME: str | None = None
    R2_PUBLIC_URL: str | None = None
    R2_PRESIGNED_EXPIRY_SECONDS: int = 7 * 24 * 3600

    # --- OKX / x402 (a2mcp/) ------------------------------------------
    OKX_API_KEY: str | None = None
    OKX_SECRET_KEY: str | None = None
    OKX_PASSPHRASE: str | None = None
    PAY_TO_ADDRESS: str | None = None

    # --- BOT Chain native-BOT payments (botchain_pay.py, a2mcp_botchain/) --
    # Separate rail from OKX/x402 above - BOT Chain has no live AgentPay
    # protocol yet (still roadmap per BOT Chain's own materials as of this
    # writing), so this is a manual pay-to-treasury-then-prove-it-with-a-
    # tx-hash flow, not an SDK integration.
    #
    # BOTCHAIN_ENVIRONMENT picks which of the two chain configs below is
    # live - one switch controls both the frontend wallet prompt
    # (/config/chain) and the backend verifier (botchain_pay._w3()), so
    # they can't drift apart.
    BOTCHAIN_ENVIRONMENT: Literal["testnet", "mainnet"] = "testnet"
    TREASURY_ADDRESS: str | None = None

    # Testnet - chain id 968, confirmed against BOT Chain's testnet
    # explorer default (scan.bohr.life also appears as a fallback default
    # in ShieldGuard's chainVerify.js, an independent corroboration).
    BOTCHAIN_TESTNET_CHAIN_ID_HEX: str = "0x3c8"  # 968
    BOTCHAIN_TESTNET_RPC_URL: str = "https://rpc.bohr.life"
    BOTCHAIN_TESTNET_EXPLORER_URL: str = "https://scan.bohr.life"

    # Mainnet - chain id 677 / 0x2a5, confirmed via chainlist.org/chain/677
    # AND ShieldGuard's own live packages/backend/.env.example
    # (RPC_URL=https://rpc.botchain.ai, CHAIN_ID=677), not just one source.
    BOTCHAIN_MAINNET_CHAIN_ID_HEX: str = "0x2a5"  # 677
    BOTCHAIN_MAINNET_RPC_URL: str = "https://rpc.botchain.ai"
    BOTCHAIN_MAINNET_EXPLORER_URL: str = "https://scan.botchain.ai"

    # BOTCHAIN_KEY_ISSUE_PRICE_BOT / BOTCHAIN_PER_CALL_PRICE_BOT removed -
    # they priced the retired API-key system (5 BOT to mint a key, 0.2
    # BOT per call). Nothing charges these anymore: /generate, /export,
    # and a2mcp_botchain/server.py's MCP tools all spend prepaid credits
    # instead (BOTCHAIN_CREDIT_PRICE_BOT and the two pack settings
    # below). See db.py and wallet_auth.py's module docstrings.
    BOTCHAIN_CONFIRMATION_BLOCKS: int = 1

    # Free STL-only preview (/preview) - no wallet, no API key, no BOT
    # payment gating it, so it needs its own hard per-IP limit. A real
    # BOT payment is a natural throttle on /generate; nothing throttles
    # this route except this setting. Deliberately stricter than
    # RATE_LIMIT_GENERATE.
    RATE_LIMIT_PREVIEW: str = "5/hour"

    # --- On-chain design provenance (contracts/DesignRegistry.sol) --------
    # DESIGN_REGISTRY_ADDRESS is set AFTER deploying - see
    # scripts/deploy_design_registry.py. Unset means anchoring is
    # silently skipped (see design_registry.py) - a missing anchor never
    # blocks a paid export from being delivered.
    #
    # ANCHOR_WALLET_PRIVATE_KEY is DEPRECATED and unused: anchoring is now
    # signed by each customer's own wallet (design_registry.py). Kept only
    # so existing env files that still set it don't fail validation.
    ANCHOR_WALLET_PRIVATE_KEY: str | None = None

    # The deployed DesignRegistry contract (env: DESIGN_REGISTRY_ADDRESS).
    # This field MUST exist: Settings uses extra="ignore", so without it
    # the Railway env var is silently dropped and design_registry.py /
    # web_app.py hit AttributeError on settings.DESIGN_REGISTRY_ADDRESS,
    # which made anchoring report "not enabled" even with the var set.
    DESIGN_REGISTRY_ADDRESS: str | None = None

    # --- Wallet sign-in (wallet_auth.py) ------------------------------------
    # Replaces the old "API key bought via a 5 BOT payment, cached in one
    # browser" identity model with a free signature-based login: connect
    # a wallet, sign a nonce message (no gas, nothing on-chain), get a
    # session. See wallet_auth.py's module docstring for the full flow.
    WALLET_NONCE_TTL_SECONDS: int = 600  # 10 minutes to complete the sign
    WALLET_SESSION_TTL_SECONDS: int = 7 * 24 * 3600  # 7 days

    # --- Credits (replaces the old per-call BOT payment model) ------------
    # /generate spends 1 credit per call (db.consume_credit) instead of
    # demanding a fresh on-chain payment every time - see
    # POST /credits/purchase. a2mcp_botchain/server.py's MCP tools use
    # the exact same mechanism now too. All three numbers below buy
    # credits the SAME underlying way, just different batch sizes:
    # pay-as-you-go is "buy 1 credit, spend it immediately." The two
    # packs intentionally have the identical per-credit price (1/1000 ==
    # 10/10000 == 0.001 BOT/credit) - a consistent bulk rate, not an
    # escalating discount - while single-credit purchases pay a 10x
    # premium for not committing to volume. Exports remain free once a
    # job exists - see GET /export/{fmt}/{job_id}'s docstring.
    BOTCHAIN_CREDIT_PRICE_BOT: float = 0.01
    BOTCHAIN_PACK_1000_PRICE_BOT: float = 1.0
    BOTCHAIN_PACK_1000_CREDITS: int = 1000
    BOTCHAIN_PACK_10000_PRICE_BOT: float = 10.0
    BOTCHAIN_PACK_10000_CREDITS: int = 10000

    # --- BOT Chain USDT (stripe_pay.py's on-chain sibling) - BLOCKED ------
    # Deliberately left unset. Do NOT hardcode a USDT contract address
    # found by web search - a wrong ERC20 address here doesn't fail
    # loudly, it verifies against a different (or fraudulent) token
    # while real customer payments go unrecognized, and there is no
    # authoritative BOT Chain USDT address confirmed as of this writing.
    # Get this from BOT Chain's own docs/explorer or from ShieldGuard's
    # existing payment config (packages/backend/connections.js or
    # webhook.js - see the BOTCHAIN_TESTNET/MAINNET comments above,
    # which already cite ShieldGuard as corroboration for the RPC/chain-
    # id values, the same source should have this). Every USDT-paid
    # route stays unbuilt until this is set to something verified.
    BOTCHAIN_USDT_CONTRACT_ADDRESS: str | None = None
    BOTCHAIN_USDT_DECIMALS: int = 6  # standard for USDT on every chain it's bridged to

    # --- Subscription: Paystack (paystack_pay.py) --------------------------
    # Replaces Stripe as of this change - see the conversation that made
    # this call ("certain limitations"). STRIPE_* below is left in place,
    # unused, rather than deleted: cheap rollback path, no data loss risk,
    # and db.subscriptions.provider already distinguishes rows written by
    # either rail so nothing conflicts if both ever coexist again.
    #
    # Mechanically different from Stripe, not just a relabeling:
    #   - No separate webhook secret. Paystack signs webhooks with the
    #     SAME PAYSTACK_SECRET_KEY used for API calls (HMAC-SHA512 of the
    #     raw body, hex digest, in the x-paystack-signature header) -
    #     confirmed against Paystack's current docs before writing
    #     paystack_pay.py, not assumed from memory.
    #   - No "Checkout Session" object. POST /transaction/initialize with
    #     a plan code returns an authorization_url to redirect to -
    #     that's the checkout_url equivalent.
    #   - Requires a real customer EMAIL, not a wallet address - Paystack
    #     has no concept of a wallet. Per the explicit architecture
    #     decision: the wallet stays the NitoCAD identity throughout,
    #     email is only what Paystack needs to run a charge -
    #     db.upsert_paystack_subscription stores wallet_address, email,
    #     paystack_customer_code AND paystack_subscription_code together
    #     so a webhook is resolved back to a wallet via the Paystack
    #     customer/subscription code, never by re-deriving anything from
    #     the email itself.
    #   - USD pricing is NOT assumed enabled. See paystack_pay.py's
    #     module docstring: initialize_subscription refuses to run in USD
    #     until PAYSTACK_CURRENCY is explicitly set to "USD" by someone
    #     who has confirmed that in the Paystack dashboard - defaulting
    #     silently to NGN (or silently treating the $5/$10/$50 figures as
    #     NGN amounts) would misprice every tier by roughly 1500x.
    #
    # PAYSTACK_PLAN_CODE_* are Plan codes (PLN_...) from the Paystack
    # dashboard (Payments > Plans) or the POST /plan API - one per tier,
    # same reasoning as Stripe's per-tier Price id.
    PAYSTACK_SECRET_KEY: str | None = None
    PAYSTACK_PLAN_CODE_STARTER: str | None = None
    PAYSTACK_PLAN_CODE_ENGINEER: str | None = None
    PAYSTACK_PLAN_CODE_PROFESSIONAL: str | None = None
    PAYSTACK_CURRENCY: str | None = None  # must be explicitly "USD" - see above; None blocks initialize_subscription entirely

    # --- Dormant: Stripe (stripe_pay.py) - superseded by Paystack above ----
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    STRIPE_PRICE_ID_STARTER: str | None = None
    STRIPE_PRICE_ID_ENGINEER: str | None = None
    STRIPE_PRICE_ID_PROFESSIONAL: str | None = None

    # --- Email sign-up via Google (google_auth.py) --------------------------
    # A SECOND, independent identity path alongside wallet sign-in, not
    # a replacement - see the conversation that scoped this: Stripe-only
    # (an email account has no wallet to pay BOT/USDT from), 1 free
    # credit on signup granted immediately (not gated on a first
    # payment, unlike the wallet flow's 10 - a real Google account has
    # more inherent friction than a free Ethereum keypair, and 1 credit
    # is a small enough blast radius even if that assumption is wrong).
    # GOOGLE_OAUTH_CLIENT_ID is NOT a secret - it's the public client id
    # Google Identity Services needs client-side too, exposed via
    # GET /config/auth precisely so it doesn't need hardcoding into the
    # static frontend file.
    GOOGLE_OAUTH_CLIENT_ID: str | None = None
    # Credits a new Google account starts with (they expire after SIGNUP_GRANT_DAYS). Once they are spent the
    # person has to upgrade to continue. A determined person with many Google accounts could collect this
    # more than once. Set EMAIL_SIGNUP_BONUS_CREDITS in the environment to change it without a deploy.
    EMAIL_SIGNUP_BONUS_CREDITS: int = 250
    # What a plain AI answer (a question, advice, a clarifying reply) costs. Previews and edits are free.
    CHAT_ANSWER_CREDITS: int = 1

    # The signup grant for Google-verified accounts is EMAIL_SIGNUP_BONUS_CREDITS above (250).
    # It is a one-time grant that expires after SIGNUP_GRANT_DAYS. At 1 credit = $0.001 of metered
    # cost, 250 credits caps the free exposure per account at about $0.25 of provider cost.
    # SIGNUP_GRANT_CREDITS is no longer read. It stays so an old environment that sets it still loads.
    SIGNUP_GRANT_CREDITS: int = 250
    SIGNUP_GRANT_DAYS: int = 90

    # Where the vehicle library keeps what it learns when someone presses Generate. Empty = data/spec_library, or memory
    # only if that cannot be written (serverless disks are wiped between runs: point this at a persistent volume).
    VEHICLE_LIBRARY_DIR: str = ""
    # A thumbs-up on a planned vehicle, or pressing Generate on it, only queues it for review. It becomes an example for
    # other people's prompts after `python spec_library.py approve <id>` (list the queue with `... pending`). Set false
    # to promote on the first paid Generate or thumbs-up (not recommended: one person's design reaches everyone's prompts).
    VEHICLE_LIBRARY_REVIEW: bool = True
    # Wall-clock limit for planning one vehicle message (all model retries together). The browser waits 170 s. One model
    # call is still limited only by AI_TIMEOUT_SECONDS. A timeout builds nothing and charges nothing.
    VEHICLE_PLAN_TIME_BUDGET_S: float = 120.0
    # Built vehicle packages are kept this many seconds so a repeat download does not rebuild them. 0 turns the cache off.
    # Per process: with several workers each keeps its own copy. Payment is always checked before the cache is used.
    VEHICLE_PACKAGE_CACHE_TTL_S: int = 900
    VEHICLE_PACKAGE_CACHE_MAX_ITEMS: int = 8

    # Bachs product ids for the two paid plans (create them in the Bachs dashboard).
    BACHS_PRODUCT_ID_PRO: str | None = None
    BACHS_PRODUCT_ID_MAX: str | None = None
    # sk_sandbox_... talks to the sandbox, sk_live_... to production.
    BACHS_API_KEY: str | None = None
    # Signing secret of the webhook endpoint you register at /webhooks/bachs.
    BACHS_WEBHOOK_SECRET: str | None = None

    @property
    def topup_packs(self) -> dict[str, dict]:
        """One-time credit packs. Prices keep each pack above the subscription
        rate of about $0.0026 per credit, so a top-up never beats a plan."""
        return {
            "starter": {"usd": "10.00", "credits": 2500},
            "growth": {"usd": "25.00", "credits": 7500},
            "scale": {"usd": "60.00", "credits": 20000},
        }

    @property
    def subscription_tiers(self) -> dict[str, dict]:
        """Keyed by our own internal tier name (stored in
        subscriptions.tier). paystack_pay.py's _TIER_BY_PLAN_CODE maps a
        webhook's plan_code back to one of these keys, same role
        stripe_pay.py's _tier_by_price_id used to play. monthly_credits
        == 0 means "no pool, pure daily allowance" (Engineer/
        Professional); > 0 means "pool that depletes, also throttled by
        the daily cap" (Starter) - see db.charge_generation for exactly
        how that distinction is used. file_exports/advanced_engineering
        are named for product/UI purposes only; nothing reads them yet
        (see the module comment above)."""
        return {
            # Current plans. weekly_credits is granted once per 7 days while the
            # subscription is paid. fallback_daily_credits is a small daily grant
            # that only pays for the cheapest model once the weekly credits run out.
            # ai_models.allowed_for_plan decides which models each plan can pick.
            "pro": {
                "bachs_product_id": self.BACHS_PRODUCT_ID_PRO,
                "stripe_price_id": None,
                "paystack_plan_code": None,
                "usd_cents": 2000,
                "weekly_credits": 1800,
                "fallback_daily_credits": 150,
                "features": {"prompt_refinement": True},
            },
            "max": {
                "bachs_product_id": self.BACHS_PRODUCT_ID_MAX,
                "stripe_price_id": None,
                "paystack_plan_code": None,
                "usd_cents": 8000,
                "weekly_credits": 9000,
                "fallback_daily_credits": 400,
                "features": {"prompt_refinement": True, "advanced_engineering": "undefined"},
            },
            # Legacy plans. Kept so existing subscribers keep working until they
            # are moved to Pro or Max. Remove once nobody is on them.
            "starter": {
                "stripe_price_id": self.STRIPE_PRICE_ID_STARTER,
                "paystack_plan_code": self.PAYSTACK_PLAN_CODE_STARTER,
                "usd_cents": 500,
                "monthly_credits": 100,
                "daily_generation_cap": 10,
                "features": {"prompt_refinement": True, "file_exports": "undefined"},
            },
            "engineer": {
                "stripe_price_id": self.STRIPE_PRICE_ID_ENGINEER,
                "paystack_plan_code": self.PAYSTACK_PLAN_CODE_ENGINEER,
                "usd_cents": 1000,
                "monthly_credits": 0,
                "daily_generation_cap": 30,
                "features": {"prompt_refinement": True, "file_exports": "undefined"},
            },
            "professional": {
                "stripe_price_id": self.STRIPE_PRICE_ID_PROFESSIONAL,
                "paystack_plan_code": self.PAYSTACK_PLAN_CODE_PROFESSIONAL,
                "usd_cents": 5000,
                "monthly_credits": 0,
                "daily_generation_cap": 150,
                "features": {
                    "prompt_refinement": True,
                    "file_exports": "undefined",
                    "advanced_engineering": "undefined",
                },
            },
        }

    # Railway auto-injects this at deploy time - no manual setting
    # needed there. Falls back to "unknown" for local runs. This is
    # what makes "same params + same templateVersion reproduces the
    # same hash" a checkable claim instead of an assumption - see
    # DesignRegistry.sol's own note on why it's required, not optional.
    @property
    def template_version(self) -> str:
        import os
        return os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")

    @property
    def botchain_rpc_url(self) -> str:
        return self.BOTCHAIN_TESTNET_RPC_URL if self.BOTCHAIN_ENVIRONMENT == "testnet" else self.BOTCHAIN_MAINNET_RPC_URL

    @property
    def botchain_chain_id_hex(self) -> str:
        return self.BOTCHAIN_TESTNET_CHAIN_ID_HEX if self.BOTCHAIN_ENVIRONMENT == "testnet" else self.BOTCHAIN_MAINNET_CHAIN_ID_HEX

    @property
    def botchain_explorer_url(self) -> str:
        return self.BOTCHAIN_TESTNET_EXPLORER_URL if self.BOTCHAIN_ENVIRONMENT == "testnet" else self.BOTCHAIN_MAINNET_EXPLORER_URL

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        v = v.upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _fall_back_to_llm_env_names(self) -> "Settings":
        """DEEPSEEK_API_KEY takes priority if both are set. LLM_* is the
        fallback for deployments (like Railway here) that only defined the
        provider-agnostic names."""
        if self.DEEPSEEK_API_KEY is None and self.LLM_API_KEY is not None:
            self.DEEPSEEK_API_KEY = self.LLM_API_KEY
        if self.LLM_BASE_URL is not None:
            self.DEEPSEEK_BASE_URL = self.LLM_BASE_URL
        if self.LLM_MODEL is not None:
            self.DEEPSEEK_DEFAULT_MODEL = self.LLM_MODEL
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def r2_configured(self) -> bool:
        return bool(self.R2_ACCOUNT_ID and self.R2_ACCESS_KEY_ID and self.R2_SECRET_ACCESS_KEY)


@lru_cache
def get_settings() -> Settings:
    """Cached so `Settings()` (which reads the environment and .env file)
    only runs once per process; call `get_settings.cache_clear()` in tests
    that need to reload with different env vars."""
    return Settings()


settings = get_settings()
