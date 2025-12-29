#!/bin/bash
# Script to remove .env from Git history
# IMPORTANT: Run this AFTER rotating your password!

set -e

echo "=========================================="
echo "Git History Cleanup Script"
echo "=========================================="
echo ""
echo "⚠️  WARNING: This will rewrite Git history!"
echo "⚠️  Anyone who has cloned this repo will need to re-clone"
echo "⚠️  Make sure you've rotated your password FIRST"
echo ""
read -p "Have you rotated your email password? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "❌ Please rotate your password first, then run this script again"
    exit 1
fi

echo ""
echo "Checking for cleanup tools..."

# Try to install git-filter-repo (best option)
if ! command -v git-filter-repo &> /dev/null; then
    echo "Installing git-filter-repo..."
    if command -v brew &> /dev/null; then
        brew install git-filter-repo
    elif command -v pip3 &> /dev/null; then
        pip3 install git-filter-repo
    else
        echo "⚠️  Cannot auto-install git-filter-repo"
        echo "   Install it manually: brew install git-filter-repo"
        echo "   Or: pip3 install git-filter-repo"
        exit 1
    fi
fi

echo ""
echo "Step 1: Backing up current state..."
git branch backup-before-cleanup 2>/dev/null || echo "Backup branch already exists"

echo ""
echo "Step 2: Removing .env from Git history..."
echo "This may take a minute..."

# Use git-filter-repo to remove .env
git filter-repo --path .env --invert-paths --force

echo ""
echo "Step 3: Cleaning up repository..."
git reflog expire --expire=now --all
git gc --prune=now --aggressive

echo ""
echo "=========================================="
echo "✅ Git history cleaned!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Verify: git log --all --full-history -- .env"
echo "   (should show nothing or only recent deletion)"
echo ""
echo "2. Force push to GitHub:"
echo "   git push origin --force --all"
echo "   git push origin --force --tags"
echo ""
echo "3. Set up GitHub Secrets (if not done already):"
echo "   - Go to Settings → Secrets → Actions"
echo "   - Add SENDER_PASSWORD with your NEW password"
echo ""
echo "4. Create local .env with NEW credentials:"
echo "   cp .env.example .env"
echo "   # Edit .env with NEW password"
echo ""
echo "⚠️  Remember: The old password is still compromised!"
echo "   GitHub keeps deleted data for 90 days."
echo "   Assume the old password is permanently compromised."
echo ""
