import argparse
import pandas as pd
import sys
import os

def main():
    parser = argparse.ArgumentParser(description="Refine the final report using the public dataset while preserving order.")
    parser.add_argument("--final", type=str, required=True, help="Path to the final result CSV (File 1)")
    parser.add_argument("--dataset", type=str, required=True, help="Path to the dataset CSV (File 2)")
    parser.add_argument("--output", type=str, required=True, help="Path to save the refined CSV")
    
    args = parser.parse_args()

    # Check if files exist
    if not os.path.exists(args.final):
        print(f"❌ Error: Could not find {args.final}")
        sys.exit(1)
    if not os.path.exists(args.dataset):
        print(f"❌ Error: Could not find {args.dataset}")
        sys.exit(1)

    # 1. Read the CSV files
    try:
        df_final = pd.read_csv(args.final)
        df_dataset = pd.read_csv(args.dataset)
    except Exception as e:
        print(f"❌ Error reading files: {e}")
        sys.exit(1)

    # 🌟 MAGIC TRICK: Add a hidden index to memorize the exact original order
    df_final['__original_index'] = range(len(df_final))

    # 2. Merge dataframes on the 'title' column (Outer Join)
    merged_df = pd.merge(
        df_final, 
        df_dataset[['pmid', 'title', 'abstract', 'url', 'pmc_id', 'open_access', 'public_access']], 
        on='title', 
        how='outer', 
        suffixes=('_final', '_dataset')
    )

    # 🌟 ENFORCE ORDER: Sort by the hidden index. 
    # Any new papers from the dataset won't have an original index (it will be NaN).
    # na_position='last' forces all these brand new, missing papers to the very bottom!
    merged_df = merged_df.sort_values(by=['__original_index'], na_position='last')

    refined_rows = []

    # 3. Process each row sequentially
    for index, row in merged_df.iterrows():
        # Handle matching failures (if a title in the final CSV is completely missing from the dataset)
        if pd.isna(row['open_access_dataset']) or pd.isna(row['public_access']):
            print(f"❌ Error: Paper '{row['title']}' was not found in the dataset CSV.")
            sys.exit(1)

        # Parse boolean values safely
        oa = str(row['open_access_dataset']).strip().lower() in ['true', '1', 't']
        pa = str(row['public_access']).strip().lower() in ['true', '1', 't']

        # Apply Open Access / Public Access Logic
        if oa is False and pa is True:
            final_oa = False
        elif oa is True and pa is True:
            final_oa = True
        else:
            # If both are False, or OA is True but PA is False, raise an exception and exit
            raise Exception(f"🔒 Closed-access paper detected: '{row['title']}'. Exiting program.")

        # 🌟 THE FIX: Pandas split 'url' into 'url_final' and 'url_dataset'.
        # Let's safely extract both.
        url_final_val = row.get('url_final')
        url_dataset_val = row.get('url_dataset')

        # Logic: Take the URL from the final result. If it's empty (like for newly appended missing papers), 
        # try to grab it from the dataset!
        if pd.notna(url_final_val) and str(url_final_val).strip() != '':
            url_val = url_final_val
        elif pd.notna(url_dataset_val) and str(url_dataset_val).strip() != '':
            url_val = url_dataset_val
        else:
            url_val = ''

        # Handle NaN values for newly appended papers
        interest_val = row['interest?'] if pd.notna(row.get('interest?')) else ''
        category_val = row['category'] if pd.notna(row.get('category')) else ''

        # Construct the refined row
        refined_rows.append({
            'pmid': row['pmid'],
            'title': row['title'],
            'abstract': row['abstract'],
            'url': url_val,
            'pmc_id': row['pmc_id'],
            'interest?': interest_val,
            'open_access': final_oa,
            'category': category_val
        })

    # 4. Create the final dataframe
    refined_df = pd.DataFrame(refined_rows)

    # 5. Order columns explicitly
    ordered_columns = [
        'pmid', 
        'title', 
        'abstract', 
        'url', 
        'pmc_id', 
        'interest?', 
        'open_access', 
        'category'
    ]
    
    # Filter to only keep columns that actually existed to prevent key errors
    final_cols = [col for col in ordered_columns if col in refined_df.columns]
    refined_df = refined_df[final_cols]

    # Create output directory if it doesn't exist
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # 6. Save the refined CSV
    refined_df.to_csv(args.output, index=False)
    print(f"✅ Successfully refined the report! Original order preserved, and missing papers appended to the end. Saved to {args.output}")

if __name__ == "__main__":
    main()