# Supabase Security Guide

By default, Supabase creates APIs that are accessible to anyone with your `anon` public key. Since this project involves scraping and writing public data to the database, you must secure the endpoints so that only your GitHub Actions (or your local machine) can write data, while ensuring unauthorized users cannot tamper with it.

## 1. Enable Row Level Security (RLS)

You must enable RLS on every table so that default access is strictly denied.

1. Go to your **Supabase Dashboard** > **Database** > **Tables**.
2. For each of the following tables (`companies`, `company_essentials`, `yearly_financials`, `quarterly_financials`), click the checkbox and select **Enable RLS**.

## 2. Setting Up Policies

Once RLS is enabled, you need to define what operations are allowed.

### Allow Public Read (Optional)
If you want to use this database as a backend for a public portfolio/analysis website, you should allow public reads:
```sql
CREATE POLICY "Allow public read" ON companies FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON company_essentials FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON yearly_financials FOR SELECT USING (true);
CREATE POLICY "Allow public read" ON quarterly_financials FOR SELECT USING (true);
```

### Deny All Public Writes
Do **not** create any `INSERT`, `UPDATE`, or `DELETE` policies for the `anon` or `public` role or authenticated users.

## 3. How the Scraper Writes Data

If there are no policies allowing inserts, how does the scraper push data?

Supabase provides two keys:
1. `NEXT_PUBLIC_SUPABASE_ANON_KEY`: Safe for browsers. Subject to RLS.
2. `SUPABASE_SERVICE_ROLE_KEY`: **Bypasses all RLS policies. Has admin privileges.**

In your GitHub Actions secrets (or your local `.env`), you must set the `SUPABASE_KEY` variable to your **`service_role`** key, not the `anon` key.

```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=eyJh... (Your Service Role Key)
```

Because the ingestion pipeline uses the `service_role` key, it will bypass RLS and successfully upsert records into the database. Meanwhile, anyone else trying to access the REST API using your public `anon` key will be strictly read-only (or entirely blocked, if you skipped step 2.1).

## Summary Checklist
- [ ] RLS enabled on all 4 tables.
- [ ] Read-only `SELECT` policies created (if you need front-end access).
- [ ] NO insert/update/delete policies created.
- [ ] Ensure `.env` and GitHub Action secrets use the `service_role` key.
