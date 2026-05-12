# pyRevit Auto Dim Tool

A pyRevit tool for creating grid and facade dimensions across selected Revit plan views.

This project started as a practical BIM automation experiment focused on reducing repetitive documentation work in Revit. The current version supports selected plan views, grid dimensions, facade dimensions, offset control, dimension type selection, and basic Revit 2025/2026 compatibility handling.

## Features

- Create grid segment dimensions
- Create grid overall dimensions
- Create facade dimensions
- Include facade openings and facade shape segments
- Select multiple plan views from the UI
- Choose Revit dimension type
- Control dimension offsets and placement order
- Basic compatibility handling for Revit 2025 and 2026

## Current Status

This is an early public version of the tool. It has been tested on sample Revit models, but real project conditions may include edge cases such as joined walls, complex facades, unusual view ranges, or non-standard families.

## Requirements

- Autodesk Revit 2025 or 2026
- pyRevit
- Windows
- A Revit model with plan views, grids, walls, and dimension types

## Installation

1. Download or clone this repository.
2. Copy the `AutoDim.extension` folder into your pyRevit extensions folder.
3. Reload pyRevit.
4. Open Revit and run the tool from the AutoDim tab.

## Usage

1. Open a Revit project.
2. Launch the Auto Dim tool from the pyRevit ribbon.
3. Select the plan views you want to process.
4. Choose the dimension type.
5. Select the dimension options: grid segment, grid overall, facade dimensions, facade segments, or facade overall.
6. Adjust offset and order values.
7. Run the tool and review the result report.

## Limitations

- Best suited for orthogonal grid and wall layouts.
- Complex curved walls are not supported.
- Facade detection may require testing on different project standards.
- Door and window references depend on family reference setup.
- Always test on a copied model before using it in production.

## Project Structure

```text
AutoDim.extension/
└── AutoDim.tab/
    └── Dimension.panel/
        └── Auto Dim.pushbutton/
            ├── script.py
            └── ui.xaml
```

## Author

Developed by Elif Bilge Bulut as part of an ongoing BIM automation and computational design portfolio.

## License

This project is licensed under the MIT License.
