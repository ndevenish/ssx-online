const path = require("path");
const HtmlWebpackPlugin = require("html-webpack-plugin");

module.exports = {
    mode: "development",
    entry: "./src/js/index",
    output: {
        filename: "bundle.js",
        path: path.resolve(__dirname, "build"),
    },

    devServer: {
        port: 3000,
        // static: {
        //     directory: path.join(__dirname, "public"),
        //     // publicPath: '/public/'
        // }
    },

    plugins: [new HtmlWebpackPlugin({ template: "src/js/index.html" })],

    resolve: {
        extensions: [".ts", ".tsx", ".js", ".jsx", ".json"],
    },

    module: {
        rules: [
            {
                test: /\.(ts|js)x?$/,
                exclude: /node_modules/,
                use: {
                    loader: "babel-loader",
                    options: {
                        presets: [
                            [
                                "@babel/preset-env",
                                { useBuiltIns: "usage", corejs: 3 },
                            ],
                            ["@babel/preset-react", { runtime: "automatic" }],
                            "@babel/preset-typescript",
                        ],
                        plugins: ["@babel/plugin-transform-runtime"],
                    },
                },
            },
            {
                test: /\.css$/,
                exclude: /node_modules/,
                use: ["style-loader", "css-loader"],
            },
            {
                test: /\.s[ac]ss$/i,
                exclude: /node_modules/,
                use: ["style-loader", "css-loader", "sass-loader"],
            },
            {
                test: /\.svg$/,
                type: "asset/resource",
            },
        ],
    },
};
