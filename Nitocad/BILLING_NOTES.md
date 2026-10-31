# NitoCAD billing, steps 1 to 4

Copy these files over the ones in your Nitocad folder, then run `pytest`.

## New environment variables
BACHS_API_KEY            sk_sandbox_... first, sk_live_... to go live
BACHS_WEBHOOK_SECRET     signing secret of the endpoint below
BACHS_PRODUCT_ID_PRO     recurring product, $20.00 USD, monthly
BACHS_PRODUCT_ID_MAX     recurring product, $80.00 USD, monthly
NITOCAD_PROVIDER_RATES   JSON of USD per million tokens by model name (optional, cost logging)

## Bachs dashboard
1. Create two recurring products, Pro at 20.00 USD and Max at 80.00 USD, billing cycle month.
2. Developer Portal > Webhooks > add https://<your-domain>/webhooks/bachs and subscribe to:
   customer.subscription.created, customer.subscription.updated, customer.subscription.deleted,
   invoice.paid, collection.succeeded
3. Copy the signing secret into BACHS_WEBHOOK_SECRET.

## New routes
GET  /billing/plans        public, plans and top-up packs
POST /billing/subscribe    {plan, email, success_url, cancel_url} -> {checkout_url}
POST /billing/topup        {pack, success_url, cancel_url, email?} -> {checkout_url}
POST /webhooks/bachs       called by Bachs

## Before you deploy
Back up the database file. Credits are imported into a new ledger on first start and the old
credit tables stay frozen. Test Pro, Max, a top-up, a cancel and a renewal in the sandbox first.
Stripe and Paystack routes are untouched. Remove them once Bachs is verified.

## Pricing page
frontend/pricing.html is rebuilt around the subscription plans: Free, Pro and Max cards, a
comparison table of features that ship today, a credit-pack section and a short FAQ. It reads
prices and allowances live from GET /billing/plans, so changing config.py changes the page.
Subscribe asks the person to sign in (wallet or Google), asks for an email if the account has
none, then sends them to the Bachs checkout. Not yet in the table because they do not exist in
the product: collaboration, priority support, a commercial license statement.

## Subscription feel (step 5)
Pro and Max are sold as unlimited engineering work plus one quiet AI usage meter.
- Chats and projects: Pro and Max use CHAT_MAX_CHATS_PAID (10000), a runaway guard. Free keeps 200.
- Daily AI message limits are wider for Pro and Max (AI_MESSAGES_PER_DAY_PAID_*). They are an abuse
  guard, tune them from real usage.
- Standard keeps working after the weekly allowance: Pro gets 150 and Max 400 DeepSeek credits a day
  (fallback_daily_credits in config.py). Worst case about $4.50 and $12 a month at $0.001 per credit.
- GET /billing/usage returns {plan, used_pct, resets_at, standard_open} for the usage bar.
- GET /ai/models now returns class and class_label (Standard or Expert) for every model.
- The pricing page shows no credit counts. Studio still shows credit prices on its buttons.

## Suggested fair-use clause for terms.html (have a lawyer review it)
"Unlimited" chats, projects and exports are subject to fair use. AI generation is metered by a
weekly allowance that resets each week. We may limit automated use, account sharing and resale,
and we may change allowances with notice.

## Studio (step 6)
studio.js and studio.html no longer show credit counts or prices. The header chip is a usage meter
(percent used), Generate and Run check are plain buttons, the model picker offers No AI, Standard,
Expert and Expert Max, locked options show the plan that unlocks them, and the boost dialog sells
Small, Medium and Large boosts by BOT price. profile.html shows the plan, percent used and reset date.
Free accounts no longer see Qwen in the studio picker because it sat in the Expert tier.

## SEO (step 7)
Run `python tools/seo_build.py` after any change to public pages. It is safe to run again. Set the
SITE_URL environment variable if your production domain is not https://nitocad.vercel.app.

What it adds
- robots.txt and sitemap.xml (12 public URLs). Private pages (profile, refine, preview, import,
  aerospace) carry noindex and stay crawlable so Google can see the tag.
- A marked block of head tags on every public page: canonical link, Open Graph and Twitter tags.
- Home page title, description and structured data (Organization and SoftwareApplication with the
  Free, Pro $20 and Max $80 offers). If you change prices in config.py, change the offers in
  tools/seo_build.py and the three plan cards in index.html.
- Four pages: text-to-cad, b-rep-vs-mesh, how-verification-works, text-to-step. Every claim comes from
  your homepage, exports page and whitepaper. The bracket table is labelled as an illustration.
- og-image.png is a branded text card, not a product screenshot. Replace it with a real screenshot
  of a model and its PASS, FAIL or UNKNOWN table when you have one (keep 1200 x 630).
- The homepage pricing section, FAQ answers and exports note no longer sell BOT credit packs.

By hand
1. Add the site to Google Search Console, verify it, submit /sitemap.xml and inspect the home page.
2. de-nitocad-updated.vercel.app: set a redirect to the main domain in that Vercel project, or leave it,
   since its pages will name the main domain as canonical once deployed there.
3. Re-check the four new pages against the live product before publishing, and add a real demo
   (prompt, model, check table, exported file) to text-to-step.html.
4. Indexing and ranking take weeks. Watch impressions, clicks and sign-ups in Search Console.

## Credit rules (step 8)
- New Google accounts start with 250 credits (EMAIL_SIGNUP_BONUS_CREDITS), expiring after 90 days. SIGNUP_GRANT_CREDITS is no longer read.
- An AI answer to a question costs 1 credit (CHAT_ANSWER_CREDITS). Previews, edits and rule-based answers stay free.
- No daily message limit on any plan. AI_MESSAGES_PER_DAY_* default to 0 (off) and can be set above 0 as a safety valve.
- Pro and Max keep the weekly allowance, boosts and plan-locked models. The answer check counts weekly, signup and top-up credits, plus the daily fallback credits on Standard only.
- At zero credits a free account sees "You have used all your credits" with Upgrade to continue. Pro and Max see the allowance card.
- Engine never calls an AI, so it is never blocked.
- 250 credits per Google account can be farmed with many accounts. Free previews call paid models with no cap, so watch cost.
- Studio viewer: orthographic, section cut and explode controls, face measuring in the chat card. mesh-measure.js and the matching studio.css rules ship in this zip.
