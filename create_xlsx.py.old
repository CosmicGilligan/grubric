import pandas as pd
import xlsxwriter
import time
import openpyxl


def create_xlsx(filename):
    # Read the CSV into a Pandas DataFrame
    df = pd.read_csv(filename, header=None, index_col=None)

    df_split = df.iloc[:, 0].str.split(' ', expand=True)
#    df['Column A'].str.split(' ', expand=True)  # Split into multiple columns
    # Creating new column names for the split data

    df_split.columns = ['Split_' + str(i) for i in range(df_split.shape[1])]

#    # Concatenate the new columns with the original DataFrame
    df = pd.concat([df_split, df], axis=1)
    newdf = df.sort_values(by='Split_1')
    df=newdf

    # Create a Pandas Excel writer using XlsxWriter as the engine.
    writer = pd.ExcelWriter("./submissions/completions.xlsx", engine="xlsxwriter")

    # Write the dataframe data to XlsxWriter. Turn off the default header and
    # index and skip one row to allow us to insert a user defined header.
    df.to_excel(writer, sheet_name="Sheet1", startrow=0, header=True, index=True)

    # Get the xlsxwriter workbook and worksheet objects.
    workbook = writer.book
    worksheet = writer.sheets["Sheet1"]

    # Get the dimensions of the dataframe.
    (max_row, max_col) = df.shape

    # Create a list of column headers, to use in add_table().
    column_settings = [{"header": str(column)} for column in df.columns]

    # Add the Excel table structure. Pandas will add the data.
    worksheet.add_table(0, 0, max_row, max_col - 1, {"columns": column_settings})

    # Adjust row heights, column widths, etc.
    left_top_format = workbook.add_format()
    left_top_format.set_align('left')  # Set horizontal alignment to left
    left_top_format.set_align('top')   # Set vertical alignment to top
    left_top_format.set_text_wrap(True)

    for x in range(0, max_row+1):
        worksheet.set_row(x, 30)  # Set row 0 height to 20

    # Set the width for all columns except the last two
    if max_col > 3:
        worksheet.set_column(0, max_col - 2, 15, left_top_format)

    # Set the width for the last two columns
    if max_col >= 3:
        worksheet.set_column(max_col - 1, max_col, 50, left_top_format)


#    worksheet.set_column('A:'max_col-2, 25, left_top_format)  # Set column B and C width to 25
#    worksheet.set_column(max_col-1:max_col, 50, left_top_format)  # Set column B width to 25

    # Add other formatting if desired (bold, colors, etc.)

    workbook.close()
    
    return

#create_xlsx(filename)

