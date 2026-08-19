-- Extensions the schema depends on.  Created before Alembic runs, because a migration
-- cannot CREATE EXTENSION without superuser rights on a managed database — on a hosted
-- provider this is the step an operator performs once, by hand.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
