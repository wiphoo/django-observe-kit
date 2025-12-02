// MongoDB initialization script for HyperDX
// This script only creates the required collections.
// User creation is handled by the init-hyperdx service via the HyperDX API
// to ensure proper password hashing (passport-local-mongoose).

db = db.getSiblingDB('hyperdx');

// Create collections (HyperDX may also auto-create these)
db.createCollection('teams');
db.createCollection('users');
db.createCollection('dashboards');
db.createCollection('alerts');
db.createCollection('saved_searches');

print('✅ HyperDX database collections initialized');
print('   User creation is handled by init-hyperdx service');
