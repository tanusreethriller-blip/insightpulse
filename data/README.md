# Data folder

`synthetic_reviews.csv` is fictional demonstration data, not real customer feedback.
Use the dashboard's sample-download button to generate a sample with dates relative to your current day.

For your own data, use UTF-8 CSV with `text`, `date`, `rating` columns.
Dates: ISO 8601; ratings: integers 1-5; review text: 5+ words, up to 6,000 characters.
Optional `review_id` values are not stored; the application generates stable internal review IDs.
Private CSVs in this directory are ignored by Git. Do not remove those ignore rules.
