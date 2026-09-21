BEGIN;
CREATE TABLE cardhobby_account (
    owner_id uuid PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    credentials_enc text,
    session_enc text,
    pending_enc text,
    state text NOT NULL DEFAULT 'unbound'
      CHECK (state IN ('unbound','connected','login_required','verification_required')),
    retry_after timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE cardhobby_account IS 'Per-product-user encrypted CardHobby credentials and sessions; never plaintext';
COMMIT;
