const path = require("path");

module.exports = {
    mode: "development",
    // entry: "./src/js/index",
    entry: "./index.js",
    output: {
        filename: "bundle.js",
        path: path.resolve(__dirname, "static"),
    },

    devServer: {
        contentBase: path.join(__dirname, "static"),
    },

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
                            "@babel/preset-react",
                            "@babel/preset-typescript",
                        ],
                        plugins: ["@babel/plugin-transform-runtime"],
                    },
                },
            },
        ],
    },
};
