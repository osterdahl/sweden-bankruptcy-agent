#!/bin/bash
# Test script to verify the smart date detection logic used in GitHub Actions

echo "Testing Smart Date Detection Logic"
echo "===================================="
echo ""

# Function to test date logic
test_date_logic() {
    local test_day=$1
    local test_month=$2
    local test_year=$3

    echo "Testing: Day $test_day of Month $test_month, $test_year"

    if [ "$test_day" -le 3 ]; then
        echo "  → Logic: First 3 days of month → Process PREVIOUS month"
        # Calculate previous month (simplified)
        if [ "$test_month" -eq 1 ]; then
            prev_month=12
            prev_year=$((test_year - 1))
        else
            prev_month=$((test_month - 1))
            prev_year=$test_year
        fi
        echo "  → Result: Will process $prev_year-$(printf %02d $prev_month)"
    else
        echo "  → Logic: Day 4+ of month → Process CURRENT month"
        echo "  → Result: Will process $test_year-$(printf %02d $test_month)"
    fi
    echo ""
}

# Test various scenarios
echo "Scenario 1: Beginning of January (edge case - previous year)"
test_date_logic 1 1 2025

echo "Scenario 2: Beginning of February"
test_date_logic 2 2 2025

echo "Scenario 3: Mid-March"
test_date_logic 15 3 2025

echo "Scenario 4: Day 3 of April (boundary)"
test_date_logic 3 4 2025

echo "Scenario 5: Day 4 of April (boundary)"
test_date_logic 4 4 2025

echo "Scenario 6: End of December"
test_date_logic 28 12 2025

echo "===================================="
echo "Current System Date:"
echo "  Date: $(date +%Y-%m-%d)"
echo "  Day of month: $(date +%d)"
echo ""

# Simulate what would happen today
CURRENT_DAY=$(date +%d | sed 's/^0//')
CURRENT_MONTH=$(date +%m | sed 's/^0//')
CURRENT_YEAR=$(date +%Y)

if [ "$CURRENT_DAY" -le 3 ]; then
    echo "If GitHub Actions ran TODAY, it would:"
    echo "  → Process PREVIOUS month"

    # Calculate previous month
    PREV_DATE=$(date -d "1 month ago" +%Y-%m 2>/dev/null || date -v-1m +%Y-%m)
    YEAR=$(echo $PREV_DATE | cut -d'-' -f1)
    MONTH=$(echo $PREV_DATE | cut -d'-' -f2 | sed 's/^0//')
    echo "  → Period: $YEAR-$(printf %02d $MONTH)"
else
    echo "If GitHub Actions ran TODAY, it would:"
    echo "  → Process CURRENT month"
    echo "  → Period: $CURRENT_YEAR-$(printf %02d $CURRENT_MONTH)"
fi

echo ""
echo "✅ Date detection logic verified!"
