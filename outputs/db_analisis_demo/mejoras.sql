CREATE INDEX IF NOT EXISTS idx_tokens_mint ON "tokens"("mint");
CREATE INDEX IF NOT EXISTS idx_tokens_created_at ON "tokens"("created_at");
CREATE INDEX IF NOT EXISTS idx_trades_token_mint ON "trades"("token_mint");
CREATE INDEX IF NOT EXISTS idx_trades_wallet ON "trades"("wallet");
CREATE INDEX IF NOT EXISTS idx_trades_block_time ON "trades"("block_time");
CREATE INDEX IF NOT EXISTS idx_my_trades_token_mint ON "my_trades"("token_mint");
CREATE INDEX IF NOT EXISTS idx_my_trades_entry_time ON "my_trades"("entry_time");
ANALYZE;
