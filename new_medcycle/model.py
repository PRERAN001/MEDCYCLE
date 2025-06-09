import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multioutput import MultiOutputRegressor
from sklearn.ensemble import RandomForestRegressor

# Load data
df = pd.read_csv('new_medcycle/Medicine_Details.csv')

# Input: Medicine Name
X = df['Medicine Name'].astype(str)

# Output: All other columns except Medicine Name
y = df.drop(columns=['Medicine Name'])

# For simplicity, convert all outputs to string (including numbers)
y = y.fillna('').astype(str)

# TF-IDF vectorization
tfidf = TfidfVectorizer()
X_tfidf = tfidf.fit_transform(X)

# Train/test split
X_train, X_test, y_train, y_test = train_test_split(X_tfidf, y, test_size=0.2, random_state=42)

# Use a regressor for all outputs (works for both text and numbers as strings)
model = MultiOutputRegressor(RandomForestRegressor(n_estimators=100, random_state=42))
model.fit(X_train, y_train.apply(lambda x: pd.factorize(x)[0]))

# Store mapping for decoding predictions
factor_maps = [pd.factorize(y[col]) for col in y.columns]

def predict_medicine_details(medicine_name):
    X_input = tfidf.transform([medicine_name])
    preds = model.predict(X_input)[0].astype(int)
    result = {}
    for i, col in enumerate(y.columns):
        # Map prediction back to original value
        values = factor_maps[i][1]
        idx = preds[i]
        if 0 <= idx < len(values):
            result[col] = values[idx]
        else:
            result[col] = "Unknown"
    return result

# Example usage
medicine = "dolo 650"
details = predict_medicine_details(medicine)
print(details)