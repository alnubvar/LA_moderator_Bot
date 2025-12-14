import pandas as pd

df = pd.read_csv("data/ml_dataset.csv")
print(df["is_ad"].value_counts())
print(df.sample(10)[["clean_text", "is_ad"]])
