#!/bin/bash
# Quick GitHub setup script for Medical Intake Bot
# Usage: bash setup_github.sh <your_github_username> <your_github_email>

if [ $# -lt 2 ]; then
    echo "Usage: bash setup_github.sh <github_username> <github_email>"
    exit 1
fi

USERNAME=$1
EMAIL=$2

echo "🚀 Setting up GitHub repository for Medical Intake Bot"
echo "=================================================="
echo "GitHub Username: $USERNAME"
echo "Email: $EMAIL"
echo ""

# Step 1: Initialize git
echo "1️⃣ Initializing git repository..."
git init
git config user.name "$USERNAME"
git config user.email "$EMAIL"

# Step 2: Add all files
echo "2️⃣ Adding files..."
git add .

# Step 3: Check status
echo "3️⃣ Checking status..."
git status

# Step 4: Create initial commit
echo "4️⃣ Creating initial commit..."
git commit -m "Initial commit: Medical Intake Bot for Fabry disease screening"

# Step 5: Create main branch
echo "5️⃣ Setting up main branch..."
git branch -M main

echo ""
echo "✅ Local setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Go to https://github.com/new"
echo "2. Create new repository named: medical-intake-bot"
echo "3. Copy the GitHub repository URL"
echo "4. Run one of these commands:"
echo ""
echo "   🔗 With HTTPS:"
echo "   git remote add origin https://github.com/$USERNAME/medical-intake-bot.git"
echo "   git push -u origin main"
echo ""
echo "   🔐 With SSH:"
echo "   git remote add origin git@github.com:$USERNAME/medical-intake-bot.git"
echo "   git push -u origin main"
echo ""
echo "📚 More info: cat GITHUB_SETUP.md"
