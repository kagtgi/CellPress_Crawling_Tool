import argparse
import pandas as pd
import sys
import os

def main():
    parser = argparse.ArgumentParser(description="Refine the final report using the public dataset.")
    parser.add_argument("--final", type=str, required=True, help="Path to the final result CSV (File 1)")
    parser.add_argument("--dataset", type=str, required=True, help="Path to the dataset CSV (File 2)")
    parser.add_argument("--output", type=str, required=True, help="Path to save the refined CSV")
    
    args = parser.parse_args()

    # Check if files exist
    if not os.path.exists(args.final):
        print(f"Error: Could not find {args.final}")
        sys.exit(1)
    if not os.path.exists(args.dataset):
        print(f"Error: Could not find {args.dataset}")
        sys.exit(1)

    # 1. Read the CSV files
    try:
        df_final = pd.read_csv(args.final)
        df_dataset = pd.read_csv(args.dataset)
    except Exception as e:
        print(f"Error reading files: {e}")
        sys.exit(1)

    # 2. Merge dataframes on the 'title' column
    # Using a left join to ensure we keep the records from the final result CSV
    merged_df = pd.merge(
        df_final, 
        df_dataset[['pmid', 'title', 'abstract', 'pmc_id', 'open_access', 'public_access']], 
        on='title', 
        how='left', 
        suffixes=('_final', '_dataset')
    )

    refined_rows = []

    # 3. Process each row to apply the access logic and extract columns
    for index, row in merged_df.iterrows():
        # Handle matching failures (if title in final is not found in dataset)
        if pd.isna(row['open_access_dataset']) or pd.isna(row['public_access']):
            print(f"Error: Paper '{row['title']}' was not found in the dataset CSV.")
            sys.exit(1)

        # Parse boolean values safely (in case pandas read them as strings)
        oa = str(row['open_access_dataset']).strip().lower() in ['true', '1', 't']
        pa = str(row['public_access']).strip().lower() in ['true', '1', 't']

        # Apply Open Access / Public Access Logic
        if oa is False and pa is True:
            final_oa = False
        elif oa is True and pa is True:
            final_oa = True
        else:
            # If both are False, or OA is True but PA is False, raise an exception and exit
            raise Exception(f"Closed-access paper detected: '{row['title']}'. Exiting program.")

        # Construct the refined row
        refined_rows.append({
            'pmid': row['pmid'],
            'title': row['title'],
            'abstract': row['abstract'],
            'url': row.get('url', ''),
            'pmc_id': row['pmc_id'],
            'interest?': row.get('interest?', ''),
            'open_access': final_oa,
            'category': row.get('category', '')
        })

    # 4. Create the final dataframe
    refined_df = pd.DataFrame(refined_rows)

    # 5. Order columns explicitly as requested:
    # pmid before title -> title -> abstract after title -> url -> pmc_id after url -> rest
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
    
    # Filter to only keep columns that actually existed in the source to prevent key errors
    final_cols = [col for col in ordered_columns if col in refined_df.columns]
    refined_df = refined_df[final_cols]

    # Create output directory if it doesn't exist
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # 6. Save the refined CSV
    refined_df.to_csv(args.output, index=False)
    print(f"✅ Successfully refined the report and saved to {args.output}")

if __name__ == "__main__":
    main()